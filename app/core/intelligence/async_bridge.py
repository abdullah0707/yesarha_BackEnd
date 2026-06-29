"""
Async Bridge — يحوّل sync generator إلى async generator حقيقي
بدون حجب event loop. ضروري لـ streaming مع مكتبات sync مثل requests.
"""
import asyncio
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=16)
_SENTINEL = object()


async def sync_gen_to_async(sync_gen_fn, *args, **kwargs) -> AsyncGenerator:
    """
    يأخذ دالة تُرجع sync generator ويحوّلها لـ async generator حقيقي.
    يدفع كل عنصر فوراً للعميل بمجرد توفره، دون حجب event loop.
    متوافق مع Python 3.10+ (يستخدم asyncio.get_running_loop بدل get_event_loop).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    q: Queue = Queue()

    def _runner():
        try:
            for item in sync_gen_fn(*args, **kwargs):
                q.put(item)
        except Exception as e:
            q.put(("__error__", e))
        finally:
            q.put(_SENTINEL)

    loop.run_in_executor(_executor, _runner)

    while True:
        try:
            item = await loop.run_in_executor(None, q.get, True, 0.5)
        except Exception:
            continue

        if item is _SENTINEL:
            return
        if isinstance(item, tuple) and len(item) == 2 and item[0] == "__error__":
            raise item[1]

        yield item
