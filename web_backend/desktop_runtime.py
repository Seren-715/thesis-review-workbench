"""Desktop-owned stdio lifecycle; no HTTP process-control endpoint."""
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Lock, Thread


def workspace_identity(root):
    resolved = str(Path(root).resolve())
    return {'workspace_root': resolved,
            'workspace_fingerprint': sha256(os.path.normcase(resolved).encode('utf-8')).hexdigest()}


def active_task_count(*services):
    """Count all live tasks, including shared workflow CLI receipts."""
    total = 0
    for service in services:
        if service is None:
            continue
        # Workflow refreshes verified external task receipts here.
        service.active()
        with service.lock:
            records = getattr(service, 'papers', None)
            if records is None:
                records = service.jobs
            total += sum(row.get('status') in {'queued', 'running', 'cancelling'}
                         for row in records.values())
    return total


class DesktopRuntime:
    def __init__(self, *services):
        self.services = services
        self.lock = Lock()
        self.accepting = True

    def require_accepting(self):
        # Called while holding the same lock as the shutdown handshake.
        if not self.accepting:
            raise RuntimeError('服务正在安全退出，暂不接受新的任务。')

    def state(self):
        with self.lock:
            return {'active_tasks': active_task_count(*self.services),
                    'accepting_tasks': self.accepting}

    def handle(self, command, server):
        if not isinstance(command, dict) or command.get('type') not in {'shutdown', 'status'}:
            return {'type': 'control', 'status': 'rejected', 'message': '不支持的桌面控制消息。'}
        if set(command) - {'type', 'force'} or command.get('force', False) is not False:
            return {'type': 'control', 'status': 'rejected', 'message': '不支持强制终止任务。'}
        with self.lock:
            count = active_task_count(*self.services)
            if command['type'] == 'status':
                return {'type': 'status', 'status': 'ready', 'active_tasks': count,
                        'accepting_tasks': self.accepting}
            if count:
                return {'type': 'shutdown', 'status': 'busy', 'active_tasks': count}
            self.accepting = False
            # BaseServer.shutdown must run outside its serve_forever thread.
            Thread(target=server.shutdown, name='desktop-safe-shutdown', daemon=True).start()
            return {'type': 'shutdown', 'status': 'accepted', 'active_tasks': 0}

    def read_stdio(self, stream, output, server):
        for line in stream:
            try:
                if len(line) > 4096:
                    raise ValueError('控制消息过长。')
                reply = self.handle(json.loads(line), server)
            except (ValueError, TypeError) as error:
                reply = {'type': 'control', 'status': 'rejected', 'message': str(error)}
            output.write(json.dumps(reply, ensure_ascii=False) + '\n')
            output.flush()
            if reply.get('type') == 'shutdown' and reply.get('status') == 'accepted':
                return
        # EOF/crashed desktop must never interrupt running model or Word work.
        # The service remains available rather than terminating other processes.

    def start_stdio(self, stream, output, server):
        thread = Thread(target=self.read_stdio, args=(stream, output, server),
                        name='desktop-control', daemon=True)
        thread.start()
        return thread
