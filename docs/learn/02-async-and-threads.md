# 第 2 篇：异步与「多线程」— Java 并发程序员的 Python 世界观

> 本篇解决三个问题：Python 的 GIL 是什么、async/await 怎么工作、
> 项目里哪些地方用了并发、为什么那么用。注解以 **AI解析** 开头。

## 1. 先讲 GIL：为什么 Python 的「多线程」和 Java 不一样

> **AI解析（GIL 是什么）**：Python（CPython 实现）有一把全局大锁 **GIL（Global Interpreter Lock）**：
> 同一时刻**只有一个线程能执行 Python 字节码**。
> - Java：8 核 CPU 开 8 个线程跑计算，真的 8 倍速（真并行）；
> - Python：8 核开 8 个线程做计算，同一时刻还是 1 个线程在跑（伪并行，只是轮流切换）。
>
> **那 Python 多线程有什么用？** 用在 **IO 密集**场景：等数据库、等 HTTP 响应时，线程会释放 GIL，
> 别的线程就能跑。所以：
> - CPU 密集（算数、压缩、加密）→ 用**多进程** `multiprocessing`（每进程独立 GIL）；
> - IO 密集（调 API、查库、读写文件）→ 用 **asyncio**（单线程事件循环，比多线程更轻）或线程池。
>
> 结论：**这个 AI 项目里 99% 的操作是「等上游大模型/等 Milvus 查库」，属于 IO 密集，所以全项目用
> async/await 而不是多线程**。这就是你读代码时到处看到 `async def` 的根本原因。

## 2. 事件循环：Python 的 Netty EventLoop

> **AI解析（事件循环模型）**：asyncio 的核心是一个**单线程事件循环（event loop）**——
> 你可以把它当成 Netty 的 EventLoop：
> - 每个 `async def` 函数（协程）遇到 `await`（等待 IO）时，就**主动让出**控制权；
> - 事件循环趁等待间隙去跑别的协程；
> - IO 完成后再回到原地继续。
>
> 关键差别：Java 线程是**抢占式**调度（操作系统随时打断你）；
> 协程是**协作式**调度（你在 `await` 处主动交权，不 await 就没法切换）。
> 一个协程如果写了耗时的同步代码（比如 `time.sleep(10)`），整个事件循环被它堵死——
> 所有用户的请求一起卡住。**这是 Python 异步最大的坑。**

```
Java 线程模型（Spring MVC，200 个请求 200 个线程）:
线程1: ──数据库查询(阻塞 50ms)──► ──解析──►
线程2: ──数据库查询(阻塞 50ms)──► ──解析──►
线程3: ──...（线程切换成本高，但互不干扰）

Python 协程模型（FastAPI，200 个请求 1 个事件循环）:
事件循环: [请求1查库(await让出)]→[请求2查库(await让出)]→[请求3查库(await让出)]
          →[请求1的库回来了,继续]→[请求2的库回来了,继续]→...
```

## 3. async/await 语法：类比 CompletableFuture

```python
# app/core/coref.py
async def resolve(query: str, history: str = "") -> str:
    try:
        model = get_chat_model()
        r = await (COREF_REWRITE_PROMPT | model).ainvoke(
            {"query": query, "history": history or "(无)"})
        text = (r.content if isinstance(r.content, str) else "").strip()
    except Exception:
        return query
    return text or query
```

> **AI解析（逐行拆）**：
> 1. `async def`：声明这是一个**协程函数**。调用它不会马上执行，而是返回一个「协程对象」，
>    就像 Java 调用 `CompletableFuture.supplyAsync(() -> ...)` 返回 Future——**任务描述**而不是结果。
> 2. `await`：绑定在「等待一个协程」上，含义是「等它的结果，等的过程把线程让给事件循环」。
>    等价 Java 的 `future.get()`（阻塞等结果），但有一个本质区别：
>    **`future.get()` 阻塞线程，`await` 不阻塞线程**——await 会让事件循环去干别的活。
> 3. `ainvoke` 里的 `a` 前缀：LangChain 的命名约定——`invoke` 是同步版，`ainvoke` 是异步版。
>    项目里凡是 `a` 开头（`ainvoke`、`astream`、`acall`）都是 async 的。
> 4. `try/except Exception:`：等价 Java `catch (Exception e)`。
>    `except` 不写具体异常类型就是抓所有——这里兜底「指代消解失败就让原句通过」，保证不阻断主流程。

> **AI解析（易错点：忘 await）**：Java 里忘了 `.get()` 拿到的是 Future 对象；
> Python 里忘了 `await`，拿到的是协程对象——**不会报错，但逻辑不会执行**，
> 后面用它的结果时才炸（`'coroutine' object has no attribute ...`）。
> 这是 Python 新手第一号错误，AI 代码审查时第一个检查点。

## 4. 核心 API 对照表（背下这张表，读项目代码基本够用）

| Python asyncio | Java 等价 | 用途 |
| - | - | - |
| `async def f()` + `await f()` | `CompletableFuture<T>` + `.get()` | 定义/等待异步任务 |
| `asyncio.create_task(coro)` | `executor.submit(task)` 不关心结果 | 丢后台跑，不阻塞当前流程 |
| `asyncio.gather(t1, t2)` | `CompletableFuture.allOf(...)` | 并发多个，全部完成后拿结果列表 |
| `asyncio.wait_for(coro, timeout=5)` | `future.get(5, SECONDS)` | 带超时等待 |
| `asyncio.sleep(1)` | `TimeUnit.SECONDS.sleep(1)`（但本质不同！） | 最典型的 await 让出点 |
| `asyncio.run(main())` | `main()` 启动（无需容器） | 启动事件循环跑协程 |
| `loop.run_in_executor(executor, fn)` | `executor.submit(fn).get()` | 把同步阻塞函数丢到线程池执行 |
| `task.add_done_callback(f)` | `future.whenComplete(f)` | 任务结束回调 |
| `async for x in agen:` | `Flux` 订阅（Reactor） | 消费异步流 |
| `yield`（async 生成器内） | Reactor 的 `Flux.create(sink)` | 产出流式数据 |

> **AI解析（asyncio.sleep vs Thread.sleep 的本质区别）**：
> `Thread.sleep(1000)` 让**线程**睡 1 秒（占着线程不放）；
> `asyncio.sleep(1)` 只是**告诉事件循环「我 1 秒后继续」**，睡的过程中事件循环去服务其他协程。
> 这是理解协程的钥匙：**await 什么不重要，重要的是「让出」这个动作。**

## 5. 项目实例 1：后台任务 + 防抖 —— 摘要生成（`app/core/summarizer.py`）

这是本项目最典型的「多线程思维」场景：**用户回复完之后，需要在后台悄悄压缩历史对话，
绝不能阻塞用户的回复**。Java 里你会用 `executor.submit(...)`，Python 里用 `asyncio.create_task`。

```python
# app/core/summarizer.py（原样摘录）
_running: dict[int, asyncio.Task] = {}   # cid -> 在跑任务(防抖:在跑不重复起)

async def maybe_schedule_summary(conversation_id: int) -> None:
    """轮结束后调用:新增消息数达阈值且无在跑任务 → create_task 后台跑,不 await(不阻塞回复)。"""
    try:
        if conversation_id in _running:
            return
        conv = await repository.get_conversation(conversation_id)
        ...
        if not await _should_summarize(conversation_id, conv):
            return
        # 上面两个 await 期间可能有并发触发已入表(TOCTOU),入表前再查一次防双起
        if conversation_id in _running:
            return
        task = asyncio.create_task(run_summary(conversation_id))
        _running[conversation_id] = task

        def _done(t: asyncio.Task) -> None:
            if _running.get(conversation_id) is t:   # 只清自己,防误清后来者
                _running.pop(conversation_id, None)
            if not t.cancelled() and t.exception() is not None:
                logger.error("summary failed conv=%s", conversation_id, exc_info=t.exception())

        task.add_done_callback(_done)
    except Exception:
        logger.exception("summary trigger check failed conv=%s", conversation_id)
```

> **AI解析（逐点拆）**：
> 1. `asyncio.create_task(run_summary(cid))` ≈ `executor.submit(run_summary, cid)`。
>    创建任务后**立即返回，不等它跑完**——调用方（用户回复路径）继续往下走。
>    **注意：create_task 的返回值必须被某个变量「接住」**，否则任务对象可能被垃圾回收而中途取消——
>    所以这里登记进了 `_running` 字典。这是 Python 的经典坑：Java 里 `submit` 返回的 Future
>    即使丢掉不接，线程池也还持有引用；Python 的 Task 如果没人引用真的会被 GC。
> 2. `_running: dict[int, asyncio.Task]` 是防抖注册表：**同一个会话已有摘要在跑就直接返回**，
>    避免用户连发几条消息时重复压摘要。
> 3. **TOCTOU 细节**：两个 `await`（查会话、判断该不该压）之间事件循环可能切走、
>    执行别的协程——别的协程可能也触发了同一个会话的摘要。所以「检查-执行」要有两段式：
>    查一次（快速短路）→ await → **再查一次**（关键时刻）。
>    TOCTOU = Time-Of-Check To Time-Of-Use，Java 并发里用 `ConcurrentHashMap.putIfAbsent`
>    原子操作解决，这里因为单线程事件循环 + 二次检查，回避了真正的竞态。
> 4. `task.add_done_callback(_done)` ≈ `future.whenComplete((r, e) -> ...)`：
>    任务结束时回调。回调里做两件事：把自己从注册表摘掉（**先判断是不是自己**——防误清后来者，
>    和 Java 里 `map.remove(key, value)` 的两参版本同款防御）；有异常就记日志。
>
> **为什么 `_done` 里要 `t.exception()`**？**Fire-and-forget 任务的异常没人接就会**
> 「静默丢失」（Python 3.11 起会打警告），这里显式捞出来打日志。
> Java 里对应的是给 executor 设 `UncaughtExceptionHandler`。

## 6. 项目实例 2：把「同步阻塞库」丢进单线程执行器（`app/kb/milvus_client.py`）

Milvus 的官方 Python 客户端 `pymilvus` 是**同步阻塞**的（gRPC 调用会把线程卡住）。
在 async 世界里调它有两个问题：① 直接调会堵死事件循环；② 多线程并发调会出诡异 bug。
项目的解法堪称教科书：

```python
# app/kb/milvus_client.py（原样摘录）
# 专用单线程执行器:同步 pymilvus(gRPC)客户端在多线程间共享会间歇静默返回空
# (async server 用 uvloop + 默认多线程 to_thread 时实测复现)。把所有 Milvus 调用
# 固定到这一个线程(客户端亦在此线程惰性创建),即单线程独占,间歇空返回根治。
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="milvus")


async def acall(fn, *args, **kwargs):
    """在专用单线程上执行同步 pymilvus 调用(供 async 在线路径用)。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, functools.partial(fn, *args, **kwargs))
```

> **AI解析（这 6 行是整篇最重要的代码）**：
> 1. `ThreadPoolExecutor(max_workers=1)`：Java 的 `Executors.newSingleThreadExecutor()`——
>    **只有一根线程的线程池**。妙就妙在「1」：所有 Milvus 调用全部串行在同一个线程上执行。
> 2. 为什么不直接 `asyncio.to_thread(fn)`？项目注释给了解释：默认的 to_thread 会从**多线程池**中
>    随机挑线程，而 pymilvus 的 gRPC 客户端**跨线程共享时会间歇性静默返回空**
>    （不报错、查不到数据，最阴的 bug）。把客户端和所有调用锁死在**同一个线程**上，
>    它内部的连接状态就永远一致——**用「单线程独占」根治并发 bug，而不是加锁**。
>    这是排障思路的典范：既然库不是线程安全的，那就别让它见到第二个线程。
> 3. `loop.run_in_executor(executor, fn)`：**桥接函数**——把同步阻塞函数丢到线程池里执行，
>    返回的 awaitable 可以被 async 代码 await。等价 Java 的
>    `CompletableFuture.supplyAsync(() -> fn(), executor)`。
>    await 期间事件循环照常服务其他请求；但因为这个池只有 1 个线程，
>    **并发来的 Milvus 调用会在池里排队**（单线程独占的代价，换来正确性）。
> 4. `functools.partial(fn, *args, **kwargs)`：提前把参数「绑定」到函数上，生成一个新函数。
>    等价 Java 的 `() -> fn(args)` 闭包改写。因为 `run_in_executor` 只接受「无参可调用对象」。
>
> **Java 类比总结**：这就是把「非线程安全的遗留组件」用单线程池包起来的标准操作，
> 和 Java 里把非线程安全对象限制在单线程中访问（thread confinement）的思路一模一样。

## 7. 项目实例 3：超时控制 —— 工具执行引擎（`app/tools/engine.py`）

一个 AI Agent 会调用各种各样的工具（自己的函数、外部 MCP 服务），
**任何一个工具卡死都可能拖垮整轮对话**，所以每个调用都要有超时和重试：

```python
# app/tools/engine.py（原样摘录,略有节选）
while True:
    try:
        result = await asyncio.wait_for(spec.tool.ainvoke(args), timeout=tool_timeout)
        content = _cap_tokens(_format_content(spec, result))
        ...
        return run
    except _TRANSIENT as e:                       # 超时/网络抖动 → 重试
        if attempt < retries:
            attempt += 1
            logger.warning("工具暂时性故障,第 %s 次重试 name=%s err=%s", ...)
            await asyncio.sleep(0.2 * attempt)     # 退避
            continue
        ...
    except Exception as e:                         # 业务异常 → 不重试,如实回灌
        ...
```

> **AI解析（逐点拆）**：
> 1. `asyncio.wait_for(coro, timeout)` ≈ `future.get(5, TimeUnit.SECONDS)`：
>    超时就抛 `TimeoutError`。项目里超时值 `tool_timeout` 按工具来源分档
>    （内置工具 5 秒、MCP 走 HTTP 10 秒），定义在 `_timeout_of(spec)`。
> 2. **重试只针对「暂时性故障」**：`_TRANSIENT = (asyncio.TimeoutError, TimeoutError, ConnectionError, httpx.TransportError)`——
>    等价 Java 里只对 `SocketTimeoutException`、`ConnectException` 这种网络瞬时故障重试，
>    业务异常（比如「订单不存在」）重试一万次也没用。**写操作（create_ticket）重试次数恒为 0**：
>    超时未必没执行成功，重复提交比失败更糟（幂等性考虑，和 Java 支付系统同款设计）。
> 3. `await asyncio.sleep(0.2 * attempt)`：退避重试（第一次 0.2s、第二次 0.4s），
>    注意在 await 期间**不阻塞事件循环**——如果这里写 `time.sleep` 就完蛋了。
> 4. `while True + continue`：等价 Java 的 `for (int attempt = 0; ; attempt++)` 死循环 + 手动 break/return。

## 8. 项目实例 4：并发收集（`app/core/read_notes.py`）

```python
# app/core/read_notes.py（原样摘录）
notes = await asyncio.gather(*(generate(k, jobs[k], model) for k in kinds))
```

> **AI解析**：`asyncio.gather(*tasks)` 把多个协程**并发执行**，等全部完成后
> **按传入顺序**返回结果列表。等价 Java：
> `CompletableFuture.allOf(f1, f2, f3).thenApply(...)` 或 `invokeAll(tasks)`。
> 注意 `*(...)` 是 Python 的解包语法（unpacking）——把生成器里的每个元素**摊开**成位置参数，
> 等价 Java 的 `list.toArray()` 后传可变参。`gather` 与前文 `create_task` 的区别：
> gather 是「等齐了再走」（主流程依赖结果），create_task 是「丢出去就不管」（主流程不等）。
> 项目里 N 张图的小注并发生成，比一张张串行快 N 倍。

## 9. 项目实例 5：异步生成器 + SSE 流式推送（`app/api/chat.py` + `runtime.py`）

AI 应用最标志性的用户体验是「打字机效果」：大模型吐一个字，前端显示一个字。
Java 里你可能用 SSE（`SseEmitter`）或 WebSocket 实现；Python 里用**异步生成器**：

```python
# app/graph/runtime.py（原样摘录,节选）
async def _stream_events(cid: int, stream_source) -> AsyncIterator[dict]:
    async for mode, chunk in get_graph().astream(
        stream_source, config, stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            msg, meta = chunk
            if meta.get("langgraph_node") in ANSWER_NODES:
                text = msg.content if isinstance(msg.content, str) else ""
                if text:
                    yield {"type": "delta", "text": text}
        elif mode == "updates":
            ...
            yield {"type": "citation", ...}
    yield {"type": "done", "conversation_id": cid}
```

```python
# app/api/chat.py（原样摘录,节选）
@router.post("/api/chat")
async def chat(req: ChatRequest):
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for ev in runtime.stream_turn(req.user_id, req.message, req.conversation_id):
                if ev["type"] == "delta":
                    yield _sse({"delta": ev["text"]})
                ...
        except Exception:
            logger.exception("图编排失败 user_id=%s", req.user_id)
            ...
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

> **AI解析（异步生成器 = 会「边生产边消费」的管道）**：
> 1. 含 `yield` 的 `async def` 是**异步生成器**：调用它不执行函数体，返回一个「可迭代的流对象」；
>    消费方每 `async for` 一次，函数体执行到下一个 `yield` 就**暂停**，
>    吐出一个值，等消费方再要时继续。Java 类比：Reactor 的 `Flux<String>`——
>    生产者按需产出、背压驱动，而不是一次性算出全部结果。
> 2. 两层生成器**嵌套套娃**：`stream_turn`（业务事件）→ `_stream_events`（图事件）→
>    `astream`（LangGraph 内部的事件流）。每层都是 `async for ... yield` 转发，
>    就像 Java 里包装流（`BufferedReader` 包 `FileReader`）一样逐层减速/转换。
> 3. `StreamingResponse(event_stream(), media_type="text/event-stream")`：
>    FastAPI 返回 SSE 响应的标准姿势，等价 Spring MVC 的 `SseEmitter`。
>    **HTTP 连接保持打开**，事件持续推给浏览器，直到流结束。
> 4. 为什么要这么写？如果等大模型完整回答完再返回，用户要盯着空白屏幕十几秒；
>    用流式，用户 1 秒内就能看到第一个字。**「流式」是 AI 应用体验的及格线。**

## 10. 常见坑清单（Java 程序员自查表）

> **AI解析（五大坑）**：
> 1. **在 async 函数里调用阻塞函数**：如 `requests.get()`（同步 HTTP 库）、`time.sleep()`、
>    同步数据库驱动——整个事件循环被堵死，所有用户一起卡。
>    解法：换成异步库（`httpx` 的 `AsyncClient`、`asyncmy`），或丢进 `run_in_executor`。
>    项目里 Milvus 就走的这条路。
> 2. **忘写 await**：协程对象被创建但从不执行，通常表现为「代码没跑但也不报错」。
> 3. **create_task 不接返回值**：任务可能被 GC 回收，跑一半神秘消失。必须登记引用
>    （参照 `summarizer._running`）。
> 4. **在协程里用普通 `dict` 共享状态**：单个事件循环线程内其实是安全的（协程只在 await 点切换），
>    但**只要 await 了就必须按「随时会被切走」的思路写代码**（参照 summarizer 的 TOCTOU 二次检查）。
> 5. **忘了 GIL 的存在**：写了个 CPU 密集的图片处理函数丢进协程，结果单核跑满、
>    并发度为零。CPU 密集要么 `run_in_executor`（让 C 扩展释放 GIL），要么上多进程。

## 11. 一句话总结

| 场景 | Java 做法 | 本项目 Python 做法 |
| - | - | - |
| 等 HTTP/DB/模型响应 | 线程阻塞等待 | `await`（让出事件循环） |
| 后台跑任务 | `executor.submit` | `asyncio.create_task` + 引用登记 |
| 并发多个任务 | `CompletableFuture.allOf` | `asyncio.gather` |
| 带超时调用 | `future.get(t, unit)` | `asyncio.wait_for` |
| 包装同步阻塞库 | 单线程 executor | `run_in_executor` + `ThreadPoolExecutor(1)` |
| 流式输出 | `SseEmitter` / Reactor | 异步生成器 `async for` + `yield` |

下一篇：**LangChain**——看这套代码如何把「调用大模型」封装成可组合的组件。
