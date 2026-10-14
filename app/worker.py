"""独立 worker 进程：领取任务、执行、心跳、租约恢复。

worker 不能自己批准费用；外部请求只通过 execution 服务（带 execution_token）。
"""
from __future__ import annotations

import argparse
import logging
import time
import threading

from app.db import init_db
from app.services import jobs
from app.settings import Settings

log = logging.getLogger("worker")


def _handler(job: dict) -> dict | None:
    """按 kind 分发。默认：纯计算任务标记成功。真实 adapter 由 execution 服务驱动。

    R05：external 任务由 execution 服务直接执行（创建时即 RUNNING），worker
    不应抢跑；若 worker 意外领到（如遗留 QUEUED），转 RECONCILING 等待核对，
    不判 FAILED——避免把已在执行的外部动作误报失败。
    """
    kind = job["kind"]
    if kind == "echo":
        return {"echo": job["input"].get("value", "")}
    if kind == "pure_compute":
        return {"ok": True}
    if kind == "external":
        raise _ExternalHandled("external 任务由 execution 服务执行，转 RECONCILING 核对")
    # 未知 kind → FAILED
    raise ValueError(f"未知任务类型：{kind}")


class _ExternalHandled(Exception):
    pass


def run_worker(*, data_dir=None, once: bool = False, lease_seconds: int = 600, registry=None):
    settings = Settings(data_dir=data_dir)
    settings.ensure_dirs()
    init_db(settings.db_path)
    from app.adapters.registry import default_registry
    registry = registry or default_registry(settings=settings)
    log.info("worker 启动，数据目录 %s", settings.data_dir)

    while True:
        # 先恢复过期租约
        recovered = jobs.recover_expired(settings.db_path)
        for r in recovered:
            log.info("恢复任务 %s → %s（%s）", r["id"], r["to"], r["reason"])

        job = jobs.claim_job(settings.db_path, lease_seconds=lease_seconds)
        if job is None:
            if once:
                break
            time.sleep(2)
            continue
        log.info("领取任务 %s kind=%s", job["id"], job["kind"])
        stop = threading.Event()
        def renew():
            while not stop.wait(max(0.05, lease_seconds / 3)):
                jobs.heartbeat(settings.db_path,job["id"],lease_token=job["lease_token"])
        pulse = threading.Thread(target=renew,daemon=True)
        pulse.start()
        try:
            if job.get("cancel_requested") and job["kind"] != "external":
                result = jobs.finish_job(settings.db_path, job["id"],
                                         state="CANCELLED",lease_token=job["lease_token"])
                log.info("任务 %s 已取消", job["id"])
                continue
            if job["kind"] == "external":
                from app.services.execution import run_attempt
                run_attempt(settings.db_path,job,registry)
                result = None
            else:
                result = _handler(job)
            if result is not None:
                jobs.finish_job(settings.db_path, job["id"], state="SUCCEEDED",
                                result=result,lease_token=job["lease_token"])
                log.info("任务 %s 完成", job["id"])
        except _ExternalHandled as exc:
            log.warning("任务 %s：%s", job["id"], exc)
            jobs.transition_job(settings.db_path, job["id"], "RECONCILING",lease_token=job["lease_token"])
        except Exception as exc:
            log.error("任务 %s 失败：%s", job["id"], exc)
            jobs.finish_job(settings.db_path, job["id"], state="FAILED",
                            error_code="WORKER_ERROR",lease_token=job["lease_token"])
        finally:
            stop.set()
            pulse.join(timeout=2)
        if once:
            break


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="xuanpin-worker")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--once", action="store_true", help="处理一个任务后退出")
    parser.add_argument("--lease-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        run_worker(data_dir=args.data_dir, once=args.once,
                   lease_seconds=args.lease_seconds)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
