"""Pure-Python prime number example."""


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


def _parse_limit(argv, write):
    if not argv:
        return 80
    try:
        limit = int(argv[0])
    except ValueError:
        _line(write, "usage: primes.py [limit]")
        return None
    if limit < 2:
        _line(write, "limit must be at least 2")
        return None
    return limit


def _is_prime(n):
    if n < 2:
        return False
    d = 2
    while d * d <= n:
        if n % d == 0:
            return False
        d += 1
    return True


def _primes(limit):
    out = []
    n = 2
    while n <= limit:
        if _is_prime(n):
            out.append(n)
        n += 1
    return out


async def main(argv=None, cwd="/", read_char=None, write=None):
    argv = argv or []
    limit = _parse_limit(argv, write)
    if limit is None:
        return

    primes = _primes(limit)
    _line(write, "Prime numbers up to " + str(limit))
    _line(write, " ".join(str(n) for n in primes))
    _line(write, "found " + str(len(primes)) + " primes")
