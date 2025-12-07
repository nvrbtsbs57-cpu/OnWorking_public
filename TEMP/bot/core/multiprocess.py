from multiprocessing import Process
from typing import Callable, Any, Optional


def start_process(target: Callable[..., Any], name: Optional[str] = None, *args, **kwargs) -> Process:
    p = Process(target=target, args=args, kwargs=kwargs, name=name)
    p.daemon = True
    p.start()
    return p
