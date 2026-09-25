"""Session-owned encoded assets. Decode, clock, and playback stay on the host."""

import base64
import io
import wave

from .model import identifier, require, number

RESOURCE_LIMITS = dict(resources=4, resource_bytes=4 * 1024 * 1024,
                      session_resource_bytes=8 * 1024 * 1024, chunk_bytes=24576)


class Media:
    def __init__(self, backend):
        self.backend = backend
        self.audio_owner = None
        self.video_count = 0

    def get(self, session, rid, ready=True):
        identifier(rid)
        require(rid in session.resources, "unknown owned resource")
        resource = session.resources[rid]
        require(not ready or resource["ready"], "resource upload is not finished")
        return resource

    async def request(self, session, op, p):
        rid = identifier(p.get("resource"))
        if op == "resource.create":
            require(rid not in session.resources and len(session.resources) < RESOURCE_LIMITS["resources"],
                    "resource exists or session resource limit reached")
            kind, size = p.get("kind"), p.get("bytes")
            require(kind in ("audio", "video"), "resource kind must be audio or video")
            require(type(size) is int and 0 < size <= RESOURCE_LIMITS["resource_bytes"], "invalid resource size")
            require(size + sum(r["size"] for r in session.resources.values()) <= RESOURCE_LIMITS["session_resource_bytes"],
                    "session resource byte limit reached")
            session.resources[rid] = dict(kind=kind, size=size, data=bytearray(), ready=False)
            return {}
        r = self.get(session, rid, ready=False)
        if op == "resource.append":
            require(not r["ready"], "resource already sealed")
            require(type(p.get("offset")) is int and p["offset"] == len(r["data"]), "unexpected upload offset")
            encoded = p.get("data")
            require(isinstance(encoded, str) and len(encoded) <= 32768, "invalid chunk")
            try:
                chunk = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("invalid base64 chunk") from exc
            require(0 < len(chunk) <= RESOURCE_LIMITS["chunk_bytes"] and len(r["data"]) + len(chunk) <= r["size"],
                    "chunk exceeds declared size")
            r["data"].extend(chunk)
            return dict(offset=len(r["data"]))
        if op == "resource.finish":
            require(not r["ready"] and len(r["data"]) == r["size"], "incomplete or already sealed resource")
            if r["kind"] == "video":
                require(self.video_count < 4, "global video limit reached")
                result = await self.backend.call("video.open", payload=bytes(r["data"]))
                r.update(handle=result["handle"], width=result["width"], height=result["height"])
                self.video_count += 1
            else:
                try:
                    with wave.open(io.BytesIO(r["data"]), "rb") as wav:
                        require(wav.getnchannels() == 2 and wav.getsampwidth() == 2 and
                                8000 <= wav.getframerate() <= 192000 and wav.getcomptype() == "NONE",
                                "audio requires stereo 16-bit PCM WAV")
                        require(0 < wav.getnframes() <= wav.getframerate() * 10, "audio limit is ten seconds")
                        r["rate"] = wav.getframerate()
                        pcm = wav.readframes(wav.getnframes())
                        require(len(pcm) == wav.getnframes() * 4, "truncated WAV")
                except (wave.Error, EOFError) as exc:
                    raise ValueError("invalid WAV") from exc
                r["pcm"] = pcm
                r["offset"] = 0
            r.update(ready=True, playing=False, seconds=0, eof=False)
            del r["data"]
            return self.status(r)
        if op == "resource.release":
            require(not any(n.get("resource") == rid for v in session.views.values() for n in v["nodes"].values()),
                    "resource still attached to a view")
            await self.release(session, rid)
            return {}
        require(r["ready"], "resource upload is not finished")
        if op == "media.status":
            await self.refresh_audio(session, rid, r)
            return self.status(r)
        require(op == "media.control", "unknown resource/media operation")
        action = p.get("action")
        require(action in ("play", "pause", "stop", "seek"), "invalid media action")
        key = (session, rid)
        if action == "play":
            require(self.audio_owner in (None, key), "audio output is owned by another player; release it first")
        if r["kind"] == "video":
            require(any(n.get("resource") == rid for v in session.views.values() for n in v["nodes"].values()),
                    "video requires an attached player node")
            if action in ("play", "pause"):
                if action == "play" and r["eof"]:
                    await self.backend.call("video.seek", handle=r["handle"], seconds=0)
                    r.update(seconds=0, eof=False)
                await self.backend.call("video." + action, handle=r["handle"])
                r["playing"] = action == "play"
            else:
                seconds = p.get("seconds") if action == "seek" else 0
                require(number(seconds, 0, 86400), "invalid seek position")
                if action == "stop":
                    await self.backend.call("video.pause", handle=r["handle"])
                    r["playing"] = False
                await self.backend.call("video.seek", handle=r["handle"], seconds=seconds)
                r.update(seconds=seconds, eof=False)
            if action == "play":
                self.audio_owner = key
            r["refresh"] = True
        else:
            await self.refresh_audio(session, rid, r)
            if action == "play" and not r["playing"]:
                if r["eof"] or r["offset"] >= len(r["pcm"]):
                    r["offset"] = 0
                await self.backend.call("audio.open", rate=r["rate"])
                try:
                    await self.backend.call("audio.queue", payload=r["pcm"][r["offset"]:])
                except ValueError:
                    await self.backend.call("audio.close")
                    raise
                self.audio_owner = key
                r.update(playing=True, eof=False, seconds=r["offset"] / (r["rate"] * 4))
            elif action in ("pause", "stop", "seek"):
                if action == "seek":
                    require(number(p.get("seconds"), 0, len(r["pcm"]) / (r["rate"] * 4)), "invalid seek position")
                if self.audio_owner == key:
                    await self.backend.call("audio.close")
                    self.audio_owner = None
                r["playing"] = False
                if action in ("stop", "seek"):
                    r["offset"] = int((p["seconds"] if action == "seek" else 0) * r["rate"]) * 4
                    r.update(seconds=r["offset"] / (r["rate"] * 4), eof=False)
        return self.status(r)

    def status(self, r):
        return {k: r[k] for k in ("kind", "ready", "playing", "seconds", "eof", "error") if k in r}

    async def refresh_audio(self, session, rid, r):
        if r["kind"] == "audio" and r.get("playing") and self.audio_owner == (session, rid):
            queued = (await self.backend.call("audio.status"))["queued_bytes"]
            r["offset"] = len(r["pcm"]) - queued
            r["seconds"] = r["offset"] / (r["rate"] * 4)
            if queued == 0:
                r.update(playing=False, eof=True)
                session.emit(dict(event="media_ended", resource=rid))
                await self.backend.call("audio.close")
                self.audio_owner = None

    async def release(self, session, rid):
        r = session.resources[rid]
        if r["ready"] and r["kind"] == "video":
            await self.backend.call("video.close", handle=r["handle"])
            self.video_count -= 1
        elif self.audio_owner == (session, rid):
            await self.backend.call("audio.close")
        if self.audio_owner == (session, rid):
            self.audio_owner = None
        del session.resources[rid]

    async def reconcile(self, sessions):
        for session in sessions:
            attached = {n["resource"] for v in session.views.values() for n in v["nodes"].values() if n["kind"] == "video"}
            for rid, r in session.resources.items():
                if r["kind"] == "video" and r.get("playing") and rid not in attached:
                    await self.backend.call("video.pause", handle=r["handle"])
                    r["playing"] = False
                await self.refresh_audio(session, rid, r)
