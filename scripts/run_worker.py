from __future__ import annotations

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    try:
        from redis import Redis
        from rq import Queue, Worker
    except ImportError as exc:
        raise SystemExit(
            "启动异步 Worker 需要安装可选依赖：python -m pip install -e \".[worker]\""
        ) from exc

    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.task_queue_name, connection=connection)
    worker = Worker([queue], connection=connection)
    worker.work()


if __name__ == "__main__":
    main()
