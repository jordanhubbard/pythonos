# Independent hosted Ruby protocol client: no Python modules or SDL bindings.
require 'socket'
require 'json'

socket = TCPSocket.new('127.0.0.1', Integer(ARGV.fetch(0)))
serial = 0
read_exactly = lambda do |count|
  data = +''.b
  while data.bytesize < count
    chunk = socket.read(count - data.bytesize)
    raise 'desktop disconnected' unless chunk
    data << chunk
  end
  data
end
call = lambda do |op, params = {}|
  serial += 1
  body = JSON.generate(v: 1, id: serial, op: op, params: params)
  socket.write([body.bytesize].pack('N') + body)
  size = read_exactly.call(4).unpack1('N')
  raise 'oversize response' unless size.between?(1, 65_536)
  reply = JSON.parse(read_exactly.call(size))
  raise reply.inspect unless reply['v'] == 1 && reply['id'] == serial && reply['ok']
  reply.fetch('result')
end
begin
  hello = call.call('hello', client: 'ruby-standalone-e2e')
  call.call('commit', revision: 1, commands: [
    { op: 'create_view', view: 'shared', title: 'Ruby application' },
    { op: 'create_node', view: 'shared', id: 'action', kind: 'button', text: 'Ruby action' }
  ])
  call.call('sync')
  STDOUT.puts(JSON.generate(features: hello['features'], state: call.call('inspect')))
  STDOUT.flush
  STDIN.gets  # Stay connected while the Python peer tests shared desktop routing.
  STDOUT.puts(JSON.generate(call.call('poll')))
  STDOUT.flush
ensure
  socket.close
end
