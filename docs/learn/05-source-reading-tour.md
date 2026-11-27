# 第 5 篇：源码带读 — 按「一次请求的生命周期」走完全项目

> 前四篇立好了知识骨架，这一篇对照真实源码逐文件走读。
> 阅读方式建议：**打开左边终端 `make dev` 跑起服务，右边 IDE 对照本文逐段看**。
> 注解以 **AI解析** 开头。本文按依赖顺序排列，从入口一路走到最底层。

## 带读路线图

```
① app/main.py          应用入口：启动时预热、挂路由
② app/config.py        配置：Pydantic 当 Spring @ConfigurationProperties 用
③ app/graph/state.py   状态定义（详见第4篇）
④ app/graph/build.py   图组装（详见第4篇）
⑤ app/graph/routing.py 路由函数（详见第4篇）
⑥ app/graph/nodes.py   最核心：12 个节点逐个读（本篇重点）
⑦ app/graph/runtime.py 运行时：输入构造、流式、分层结算（本篇重点）
⑧ app/core/llm.py      LLM 封装（详见第3篇，本篇看两个新细节）
⑨ app/core/summarizer.py / retrieval.py / kb/milvus_client.py  异步实例回顾
⑩ app/tools/engine.py  工具引擎：权限门与注入参数（本篇新增）
⑪ app/api/chat.py      SSE 接口（详见第2篇）
```

## ① `app/main.py` — 应用入口：lifespan 就是启动钩子

```python
# app/main.py（原样摘录）
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动预热 Milvus:加载集合并轮询直到可检索,避免首个用户请求撞上 Standalone 的加载竞态
    (集合 load 在后台异步完成,未就绪时检索会静默返回空)。预热失败不阻塞启动。"""
    try:
        from app.core import retrieval
        for _ in range(15):
            hits = await retrieval.search_knowledge("退货运费", strategy="bm25", top_k=1)
            if hits:
                logger.info("Milvus 预热完成,集合可检索")
                break
            await asyncio.sleep(1)
        else:
            logger.warning("Milvus 预热超时(15s)未返回结果,继续启动")
    except Exception:
        logger.warning("Milvus 预热异常,继续启动", exc_info=True)
    from app.tools import registry as tool_registry
    tool_registry.scan_builtin()   # ch08:内置工具服务启动时登记
    _check_context_budget()
    await runtime.init_graph()
    yield
    await runtime.close_graph()
```

> **AI解析（逐点拆）**：
> 1. `@asynccontextmanager` + `yield`：把「启动逻辑」和「关闭逻辑」写在一个函数里，
>    `yield` 之前是启动、之后是关闭。等价 Spring Boot 的
>    `@PostConstruct init()` + `@PreDestroy destroy()` 两件套，Python 用一个函数表达。
> 2. **为什么先预热 Milvus**：向量库「集合加载」是后台异步完成的，
>    没加载完时**检索会静默返回空**（不报错！）——如果第一个用户请求正好撞上这个窗口，
>    他会得到一句莫名其妙的「没查到」。预热就是「启动时先自己查一次，
>    等它真的能查了再开门接客」。这是典型的**静默失败防御**。
> 3. `for _ in range(15): ... else:`：Python 的「for-else」语法——循环**正常走完**（没 break）
>    才执行 else。这里=「重试 15 次还没热好就打警告」。Java 里要靠布尔标志 + if 模拟。
> 4. `await asyncio.sleep(1)`：等一下再试（第 2 篇讲过，不阻塞其他协程——不过这是启动阶段，
>    本来就没人并发）。
> 5. 后面 `scan_builtin()`（扫描工具）→ `_check_context_budget()`（算上下文预算）→
>    `init_graph()`（编译图），全是启动期一次性的初始化。**幂等 + 顺序敏感**。
>
> 最后强调：整个函数在 `yield` 前**任何一步失败都不该炸掉进程**（try/except 包住预热、
> 预算不够只告警）——启动时尽量降级可用，而不是「一个零件没就绪整个服务起不来」。

## ② `app/config.py` — 配置类：必填字段是「故意」的

```python
# app/config.py（原样摘录）
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chat_model: str            # 必填,故意不给默认值
    chat_base_url: str
    chat_api_key: str
    ...
    token_budget: int = 2000
    embed_model: str = "BAAI/bge-m3"
    max_agent_steps: int = 6   # ReAct 环最大步数(封顶,超则兜底)
    evidence_confidence_threshold: float = 0.26  # 校准回填,不拍脑袋


settings = Settings()
```

> **AI解析**：
> 1. `BaseSettings` 自动从 `.env` 文件和环境变量读取：字段名 `chat_model` 对应
>    环境变量 `CHAT_MODEL`（大小写不敏感）。等价 Spring Boot 的
>    `@ConfigurationProperties(prefix="")` + `application.yml`。
> 2. **必填 vs 默认值的划分有讲究**（看长注释）：模型名/地址/密钥**不给默认值**——
>    给了默认值等于替用户猜，猜错了不会启动时报错，而是第一次调模型时抛个看不懂的
>    401；不给默认值，缺了**启动时**就报 `chat_model Field required`，直接指出病根。
>    **配置错误要 fail-fast，在最近的地方报错**——这是十二要素应用（12-factor）的标准姿势。
> 3. 调优常量（top_k、步数、阈值）**留在代码里带默认值**——它们是代码的一部分，
>    跟着 git 走。两类配置两种策略，这个划分值得学。
> 4. 文件末尾 `settings = Settings()`：**模块级单例**，全项目 `from app.config import settings`
>    用的都是这一个实例。等价 Java 的 `@Bean` 单例配置类。

## ③~⑤ `state.py` / `build.py` / `routing.py`

这三个文件（状态定义、图组装、路由函数）在第 4 篇已经逐行讲透，
此处只留「阅读时的关注点」：

> **AI解析（三文件复习）**：
> - `state.py`：看 `Annotated[类型, reducer]`——messages 是「追加」，trace 是「合并」，
>   其余是「覆盖」。看 `total=False`：所有键可选。
> - `build.py`：看五个条件边的映射表（意图→五出口、闸→强弱、环→继停），
>   这就是整个应用的**业务决策树**，一张纸就能画出来。
> - `routing.py`：看 `INTENT_TO_ROUTE` 单一来源 + `should_continue` 的三段判断
>   （无 tool_calls 停 / 步数封顶停 / 否则继续）。

## ⑥ `app/graph/nodes.py` — 核心节点逐个读（本篇重头戏）

### 6.1 辅助函数：`_user_text` 与 `_history_text`

```python
# app/graph/nodes.py（原样摘录）
def _user_text(state) -> str:
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            return m.content or ""
    return ""


def _history_text(state, max_turns: int = 6) -> str:
    """摘要行 + 滑窗内最近若干轮(不含本轮最后一条 human),供 coref/意图读上下文。..."""
    msgs = memory.build_window(state.get("messages", []),
                               state.get("summary_upto_msg_id") or 0,
                               state.get("layer1_from_msg_id") or 0)
    prior = msgs[:-1] if msgs else []
    lines = []
    for m in prior[-max_turns:]:
        role = "用户" if isinstance(m, HumanMessage) else "客服"
        text = m.content if isinstance(m.content, str) else ""
        if text:
            lines.append(f"{role}:{text}")
    body = "\n".join(lines)
    head = memory.summary_line(state.get("summary"))
    return f"{head}\n{body}".strip() if head else body
```

> **AI解析**：
> - `reversed(...)` 从最新往回找第一条 HumanMessage = 「用户刚说的那句话」。
>   `m.content or ""`：content 理论上可能是复杂结构（多模态），兜底成空串。
> - `prior = msgs[:-1]`：Python 切片，**取掉最后一个元素**（-1 是「最后一条」，
>   `[:-1]` = 「从头到倒数第一个之前」）。等价 Java `list.subList(0, list.size()-1)`。
>   `prior[-max_turns:]` = 「取最后 max_turns 条」。**Python 的负数索引是读代码高频点**：
>   `state["messages"][-1]` 出现过很多次——「最后一条消息」，等价 Java `list.get(list.size()-1)`。
> - 最后拼「摘要行 + 最近几轮对话」——给指代消解和意图识别当上下文。
>   **注意它用的是 `build_window`（三层上下文的滑窗渲染），不是全量历史**——
>   第 7 章的上下文管理从这里就已经介入了。

### 6.2 `resolve_reference` 与 `classify_intent`：每轮必经的两站

```python
# app/graph/nodes.py（原样摘录）
async def resolve_reference(state) -> dict:
    """指代消解 + Query 改写(合一):吃最近几轮历史把半截话补成完整问句;已完整则透传。..."""
    query = _user_text(state)
    history = _history_text(state)
    logger.info("history_ctx conv=%s(摘要行+滑窗,coref/意图共用)\n%s",
                state.get("conversation_id"), history or "(无历史)")
    resolved = await coref.resolve(query, history)
    mode = "rewrite" if resolved != query else "passthrough"
    return {"resolved_query": resolved, "trace": {"coref": mode}}
```

> **AI解析**：
> - 「指代消解」是客服场景的刚需：用户第二句说「那它多少钱？」，得靠历史把它补成
>   「猫粮多少钱？」。实现就是一次大模型调用（结构化程度低，所以用普通文本
>   `coref.resolve`，第 3 篇的 `COREF_REWRITE_PROMPT | model` 管道）。
> - `mode = "rewrite" if resolved != query else "passthrough"`：Python 的三元表达式
>   （`值1 if 条件 else 值2`），等价 Java `resolved != query ? "rewrite" : "passthrough"`。
>   记入 trace——**「这轮到底改没改」是验收时的重要观测点**。
>
> **classify_intent**（第 4 篇已详读）注意两点：它吃 `resolved_query`（消解后的完整句）
> 而不是原话；结果同时写回 State 三个字段（intent/confidence/route），
> 其中 route 供 `_agent_messages` 判「要不要注入订单数据」，路由条件边也用同一个映射表。

### 6.3 `main_agent` 的三个隐藏知识点（重点）

```python
# app/graph/nodes.py（原样摘录）
async def main_agent(state, config=None) -> dict:
    specs = await registry.get_all_specs()   # ch08 现问现拿:内置+MCP 合并清单,每轮现拉
    model = get_chat_model(streaming=True).bind_tools([s.tool for s in specs])
    msgs = _agent_messages(state)
    _log_model_context(state, msgs)
    ai: AIMessage = await model.ainvoke(msgs, config)
```

> **AI解析（知识点 1：为什么函数签名多了个 `config=None`）**：
> 别的节点都是 `async def xxx(state)`，只有 `main_agent` 是 `(state, config=None)`。
> 因为 LangGraph 允许节点接收可选的第二个参数 `config`（访问 thread_id、
> 回调 Langfuse 追踪等），`model.ainvoke(msgs, config)` 把 config 透传给 LangChain——
> **Langfuse 的调用链追踪就是靠 config 里的 callbacks 传下去的**。
> 这也让 `main_agent` 的单测需要自己造 config（或传 None）。

```python
# app/graph/nodes.py（_agent_messages 原样摘录）
def _agent_messages(state) -> list:
    """拼装顺序固定(ch07):人设+红线 system → 滑窗原文 → 摘要与本轮材料(紧跟用户那句)。..."""
    window = memory.build_window(state.get("messages", []),
                                 state.get("summary_upto_msg_id") or 0,
                                 state.get("layer1_from_msg_id") or 0)
    turn_ctx = _turn_context(state)
    if turn_ctx:
        window = _with_turn_context(window, turn_ctx)
    return [SystemMessage(AGENT_SYSTEM), *window]
```

> **AI解析（知识点 2：只允许一条 SystemMessage + 前缀缓存）**：
> 注释里讲了一个非常「AI 工程化」的坑：
> 上游把多条 system 消息**合并渲染到头部**，如果摘要单独放第二条 SystemMessage，
> 它会被拼到人设后面，**把工具 schema 挤到可变内容之后**——
> 而 prompt caching（前缀缓存）按「渲染后的精确前缀」匹配，前缀一变整段缓存作废
> （实测 cache_read 从 2048 掉到 0）。所以摘要得跟着证据走「用户侧那条消息」。
> **这是纯为省钱的架构决策**：同样的逻辑换个拼法，成本差一个量级。
> `[SystemMessage(AGENT_SYSTEM), *window]` 里 `*window` 把列表摊开合并成一个新列表
> （等价 Java `ListUtils.union(List.of(sys), window)`）。

```python
# app/graph/nodes.py（_with_turn_context 原样摘录,节选）
def _with_turn_context(window: list, turn_ctx: str) -> list:
    """把本轮材料插在滑窗里最后一条用户消息之后。
    插在这个位置而不是整个列表末尾,是为了 ReAct 轮内也能命中前缀缓存:..."""
    msg = HumanMessage(turn_ctx, id=TURN_CTX_ID)
    for i in range(len(window) - 1, -1, -1):
        if isinstance(window[i], HumanMessage):
            return [*window[:i + 1], msg, *window[i + 1:]]
    return [*window, msg]
```

> **AI解析（知识点 3：插入位置的缓存账）**：
> 「本轮材料」（检索证据/订单数据）不追加在消息列表**末尾**，而是插在
> 「最后一条用户消息之后」。why？ReAct 第 2 步的消息列表是
> `[Sys, 用户问, 材料, AI, Tool]`——它**完整包含**第 1 步的 `[Sys, 用户问, 材料]` 作前缀；
> 如果追加到末尾就变成 `[Sys, 用户问, AI, Tool, 材料]`，公共前缀只到「用户问」，
> 缓存命中率暴跌。**为了省钱，连一个插入位置都要算计**——这是 AI 应用和传统后端
> 完全不同的工程维度。
> 代码手法：倒序 for + 切片拼接；`[*a, b, *c]` 是列表解包拼接，等价
> `Stream.concat(...)`。`id=TURN_CTX_ID` 这个哨兵 id 只用于日志区分
> （注释解释：只能用 id 不能用 name——name 会被序列化进请求改变字节，id 不会；
> **again：省缓存**）。

### 6.4 `agent_tools`：唯一「写操作」的安全通道

（第 4 篇读过 interrupt 机制，这里聚焦三个安全细节）

```python
# app/graph/nodes.py（原样摘录,节选）
    ticket_calls = [tc for tc in last.tool_calls if tc["name"] == "create_ticket"]
    tspec = specs.get("create_ticket")
    decision = None
    if ticket_calls and tspec is not None \
            and engine.validate_args(tspec, dict(ticket_calls[0].get("args") or {})) is None:
        first_args = ticket_calls[0]["args"]
        decision = interrupt({"type": "confirm_ticket",
                              "preview": {"ticket_type": first_args.get("ticket_type", "咨询"),
                                          "description": first_args.get("description", "")}})
```

> **AI解析（安全细节 1：先验证再中断）**：只有参数**通过 JSON Schema 校验**
> （`validate_args(...) is None`）才弹确认卡；参数不齐就不弹，交给执行引擎
> 「校验拦下」回灌错误说明，让模型自己向用户追问。**别把半成品推给用户确认。**
> 列表推导式 `[tc for tc in last.tool_calls if ...]` 又出现了（第 1 篇讲过）。
>
> **安全细节 2**（在下面循环里）：`submit_refund` 被拦成前端表单之前，
> 也要先过 `business.owns_order(uid, ...)` 校验——注释解释了原因：
> 它**不进执行引擎**（被拦截了），所以引擎里的归属校验对它是盲区，
> **必须在拦截前补一道**，否则退款入口成了绕过校验的后门。
> 这叫「安全边界要覆盖所有执行路径」——每个绕过正常通道的特殊分支，
> 都要单独想一遍「少了哪些检查」。Java 世界里 JWT 过滤器被某个
> `permitAll` 路径绕过的漏洞，是同一类错误。

```python
# app/graph/nodes.py（原样摘录,节选）
        elif tc["name"] == "create_ticket" and decision is not None:
            if tc is ticket_calls[0]:
                # resume 值形状守卫:同一 resume 端点服务两种中断,错配一律按未确认处理
                if isinstance(decision, dict) and decision.get("confirmed"):
                    run = await engine.execute_tool_call(tc, cid, specs, confirmed=True,
                                                        user_id=uid)
                else:
                    run = await engine.execute_tool_call(
                        tc, cid, specs, confirmed=False, user_id=uid,
                        deny_note="用户在工单预览卡片上点了取消,本次不建单。请勿再发起,除非用户再次明确要求。")
```

> **AI解析（安全细节 3：resume 值得信吗？不信）**：
> resume 回传的 `decision` 是**前端给的数据**，同样不可信：
> - 形状守卫 `isinstance(decision, dict) and decision.get("confirmed")`——
>   「传了 order_id 字符串」这种错配一律当「未确认」，**宁可让用户重来，不让节点炸**；
> - 取消时给模型的指令也写得很清楚（`deny_note`）：「取消后不许自动重试，
>   除非用户再次明确要求」——**用自然语言给模型划红线**。
> 另外 `if tc is ticket_calls[0]` 用的是 `is`（同一对象引用），
> 防的是「一批 tool_calls 里混了多个 create_ticket」的情况（其余直接忽略）。

### 6.5 `fallback_reply`：失败也是生产线的一部分

```python
# app/graph/nodes.py（原样摘录,节选）
async def fallback_reply(state) -> dict:
    """置信度兜底:证据弱回兜底话术,并把问题落低置信池留给数据飞轮..."""
    source = state.get("fallback_source") or "retrieval_low_conf"
    ...
    # 快照语义三态:走了检索且有召回=列表;走了检索但零命中=[](审核页显示「已检索,零命中」,
    # 这是"真缺知识"的强信号);没走检索(非知识路兜底,fallback_source 为空)=NULL。
    walked_retrieval = bool(state.get("fallback_source"))
    snapshot = state.get("retrieved_snapshot") or ([] if walked_retrieval else None)
    await repository.insert_low_confidence(
        state.get("conversation_id"), _user_text(state), source, reason,
        retrieved_chunks=snapshot,
    )
```

> **AI解析**：答不上来的问题不是丢掉，而是**带上下文落池**：
> 落库时记下「问题 + 为什么拒答（reason）+ 召回了什么（snapshot）」，供人工审核后补进知识库
> （第 9 章数据飞轮的输入）。
> 最妙的是 `snapshot` 的三态设计：`列表`/`[]`/`None` 分别代表
> 「检索了有货但答砸了」「检索了确实没有」「根本没检索」——
> **三个不同的业务信号用三种数据形态编码**，审核员一眼能分辨问题的性质。
> 这种「数据结构承载语义」的设计品味，比写注释说清楚更高级。

## ⑦ `app/graph/runtime.py` — 图的「运行时」（重头戏续）

### 7.1 `_graph_input`：checkpointer 的「跨轮残留」陷阱

```python
# app/graph/runtime.py（原样摘录,节选）
def _graph_input(user_id: str, message: str, cid: int, msg_id: int,
                 summary: str, summary_upto: int, layer1_from: int = 0) -> dict:
    # 显式重置每轮的输出通道:checkpointer 按 thread_id 持久化 State,这些无 reducer 的标量
    # 会跨轮残留(如上一轮闲聊/投诉的 answer、建工单可选项串到本轮),须在入口清零。
    # messages 有 add_messages reducer(追加=跨轮历史),不清;steps/tokens_used 是本轮预算。
    return {"messages": [HumanMessage(message, id=f"db-{msg_id}")], "user_id": user_id,
            "conversation_id": cid, "steps": 0, "tokens_used": 0,
            "answer": "", "suggested_actions": [], "evidence": "", ...,
            "trace": None}  # merge 通道:None=重置哨兵(见 merge_dict),清上一轮 trace 残留
```

> **AI解析（这是 checkpointer 模式最大的坑）**：
> State 是跨轮持久化的，所以**上一轮写进 State 的值会「残」到这一轮**：
> 上轮是投诉路由写了 `answer=投诉话术`，本轮如果不重置，`resolve_answer`
> 可能把上轮的话术又发一遍。所以入口必须把「本轮输出通道」全部清零。
> 但注意哪些**不**清：
> - `messages` 不清（reducer 是追加——跨轮历史就是靠这个）；
> - `user_id` 不清（查出来刷新也行）；
> - 而 `steps`/`tokens_used` 重置为 0（它们是「本轮预算」，不是历史累计）。
>
> **判断标准**：这个字段是「本轮的临时输出」还是「跨轮的累积状态」？
> 前者入口清、后者靠 reducer 自然合并。**设计 State 时每个字段都要回答这个问题**——
> 项目把这条纪律写进了注释，你未来自己设计 State 时也要列这张表。
> （`HumanMessage(message, id=f"db-{msg_id}")` 的 id 是第 7 章滑窗的「锚点」，
> 把 LangGraph 消息和 MySQL 消息 id 对上的暗号。）

### 7.2 `stream_turn` / `_stream_events`：流式事件怎么转成 SSE

（第 2 篇读过 async 发电机 + SSE 部分，这里补「事件分发逻辑」）

```python
# app/graph/runtime.py（原样摘录,节选）
async for mode, chunk in get_graph().astream(stream_source, config,
                                             stream_mode=["messages", "updates"],):
    if mode == "messages":
        msg, meta = chunk
        if meta.get("langgraph_node") in ANSWER_NODES:      # 只有 main_agent 的 token 进 delta
            text = msg.content if isinstance(msg.content, str) else ""
            if text:
                yield {"type": "delta", "text": text}
    elif mode == "updates":
        if "__interrupt__" in chunk:
            payload = chunk["__interrupt__"][0].value
            ev = {"type": "interrupt", "kind": payload.get("type", ""), "conversation_id": cid}
            for k in ("orders", "preview"):
                if payload.get(k) is not None:
                    ev[k] = payload[k]
            yield ev
            return                                              # 中断即结束本次流
        for node, upd in chunk.items():
            if node in DETERMINISTIC_ANSWER_NODES and upd.get("answer"):
                yield {"type": "delta", "text": upd["answer"]}
            if node in CITATION_NODES and upd.get("citations"):
                yield {"type": "citations", "items": upd["citations"]}
            if node == "agent_tools":
                for m in upd.get("messages", []):
                    name = getattr(m, "name", None)
                    if name and name != "submit_refund":
                        yield {"type": "tool", "name": name}
```

> **AI解析（三组常量 + 分流表）**：
> 文件顶部定义了三个集合，就是「事件路由表」：
> ```python
> ANSWER_NODES = {"main_agent"}                    # 模型 token 流的来源节点
> DETERMINISTIC_ANSWER_NODES = {"script_reply", "complaint_reply", "fallback_reply"}
> CITATION_NODES = {"retrieve_knowledge", "retrieve_policy"}
> ```
> - 流式 token 只放行 `main_agent`（其他节点的模型调用 token 不进 delta，
>   否则意图识别等内部调用的 token 会漏给用户）；
> - 确定性节点没有 token 流，它们的整块答复在 `updates` 里一次性发（`type: delta` 同管道）；
> - `__interrupt__` 块 = 图暂停了，转成 `interrupt` 事件推给前端，**然后 `return` 结束本次流**
>   （前端点选后走 `/api/actions/resume` 开新流续跑）；
> - `submit_refund` 不发 tool 帧（它被拦成前端表单，不是真工具执行）——细节处处是语义。
>
> **这就是「图的事件」到「前端协议」的翻译层**：图对内吐的是通用块，
> 这里翻译成 `{type: delta/citations/tool/interrupt/done}`——
> 前端 `chat.py` 再翻译成 SSE 帧。**三层各自解耦，前端不懂 LangGraph，图不懂 SSE。**

## ⑧ `app/core/llm.py` — 两个值得回看的细节

（大框架第 3 篇已讲，此处补两个「细节控」）

> **AI解析（细节 1：槽位 fallback）**：`resolve_slot("summary")` 的逻辑是
> 「summary_model 空 → 回落 chat_model」。**同一个功能在不同配置粒度下都能跑**：
> 只用一家上游 → 一个都不用填；想把摘要换到便宜模型 → 只填两个字段。
> 这种「渐进式配置」比「全必填」亲和得多。
>
> **AI解析（细节 2：`_diff_usage` 的滑动窗口）**：上游每个流式 chunk 都重报
> 「累计 token」而不是「增量」，LangChain 又把它们相加——数字被放大到 chunk 数倍。
> 修复思路：记录上一个 chunk 的累计值，本 chunk 减上一个 = 增量；
> 发现数值**变小**（新流开始）就把基准清零。这是**流式数据对账**的通用手法，
> 和 Java 监控系统里处理「累计计数器」（Micrometer Counter / Prometheus counter）
> 的 `rate()` 计算原理完全一样——**收到累计值先差分再聚合**。

## ⑨ 异步实例回看：`summarizer.py` / `retrieval.py` / `milvus_client.py`

（第 2 篇已逐行讲解，此处只作定位提醒）

> **AI解析**：读这三个文件时对照第 2 篇的实例号：
> - `summarizer.py` = 实例 1（create_task 后台任务 + 防抖 + TOCTOU 二次检查）；
> - `milvus_client.py` = 实例 2（单线程 ThreadPoolExecutor + run_in_executor 桥接）；
> - `retrieval.py` 里还有个小递归值得看：`split_clauses` 拆出的每个子句
>   **各自再调一次 `search_knowledge`（split=False 防无限递归）**，
>   然后 `_merge_round_robin` 轮转合并——「一问多意图」的检索策略。
>   递归调用自己时**关闭拆分开关**是防止无限递归的标准手法。
> - 另外 `arrange_head_tail`（`[items[0], *items[2:], items[1]]`）：
>   把第 2 名放到**最后**——防「lost in the middle」（模型对长上下文中间内容注意力差，
>   首尾最可靠）。这是检索排序的经典 trick。

## ⑩ `app/tools/engine.py` — 权限门与参数注入（本篇新增）

（第 2 篇读过超时/重试，本篇聚焦安全设计）

```python
# app/tools/engine.py（原样摘录,节选）
    # ③ 权限门:写操作必须带确认令牌(interrupt 确认后由 agent_tools 传入),模型绕不过
    if spec.permission == "write" and not confirmed:
        note = deny_note or "该写操作需要用户确认,未确认前拒绝执行。请勿再次发起,除非用户明确要求。"
        run = _run(False, f"{name} 未执行:{note}", "权限拒绝")
        ...
        return run

    # 注入参数一律在校验之后盖上去:它们不在模型可见的 schema 里,模型填了也算数不得。
    # 身份尤其如此——让模型能填 user_id,一句「我是客服主管」就可能把它改写掉。
    if spec.inject_conversation:
        args["conversation_id"] = conversation_id
    if spec.inject_user_id:
        args["user_id"] = user_id
```

> **AI解析（AI 安全的两个核心原则，全部浓缩在这 20 行里）**：
> 1. **权限门（确认令牌）**：`create_ticket` 标记为 write 工具，
>    `confirmed` 参数**只有 `agent_tools` 在 interrupt 确认后才能传 True**——
>    签名里注释写明「模型自己无法绕过权限门」。这是**可信计算基（TCB）思想**：
>    安全决策放在模型控制不到的地方。模型再怎么会说，也传不了这个参数。
> 2. **参数注入（覆盖式）**：`user_id`/`conversation_id` 不在给模型的 schema 里
>    （`InjectedToolArg` 从 schema 排除），执行前由引擎**直接盖上真实值**——
>    就算模型幻听硬填一个 `user_id`，`args["user_id"] = user_id` 也把它盖掉。
>    「身份从可信通道来」= Java 里从 SecurityContext 取当前用户，绝不信任请求体里的身份字段。
>
> **AI 应用的安全模型与传统应用同构**：所有输入（用户话术、模型生成、前端回传）
> 都不可信；可信的只有服务端自持的数据（会话表里的 user_id、确认令牌）。
> 「提示词注入」听起来很新，防御手段还是老一套：**别让不可信数据进入决策，
> 决策点前移到自己手里**。

## ⑪ `app/api/chat.py` — 最后一公里

（第 2 篇实例 5 读过 SSE 主体，此处看异常分诊）

```python
# app/api/chat.py（原样摘录,节选）
        except runtime.ConversationNotFound:
            for f in _sse_error("会话不存在"):
                yield f
            return
        except SQLAlchemyError:
            logger.exception("数据库错误 user_id=%s", req.user_id)
            for f in _sse_error("数据库暂时不可用,请稍后重试"):
                yield f
            return
        except Exception:
            logger.exception("图编排失败 user_id=%s", req.user_id)
            for f in _sse_error("上游模型暂时不可用,请稍后重试"):
                yield f
            return
```

> **AI解析**：**分类型兜底**——会话不存在（客户端问题）、数据库故障（自身依赖）、
> 其他（大概率模型上游问题）分别给不同的用户提示。日志里带 `user_id` 便于排障。
> **流式接口的异常处理有个特殊难点**：响应头（200）已经发出去了，
> 没法再用 HTTP 状态码报错——只能**把错误也变成流里的一个事件**
> （`event: error`），前端按事件处理。这是所有 SSE/WebSocket 接口的通用约束，
> 记住这个模式：**流式场景的错误是「消息」不是「状态码」。**

## 带走这张「读代码检查清单」

以后你读任何 AI Agent 项目，按这张清单问一遍，基本能摸透：

> **AI解析（十问）**：
> 1. **入口在哪**：找 lifespan/startup，看启动预热、单例初始化。
> 2. **状态是什么**：State/内存里流转哪些字段？哪些跨轮、哪些本轮？
> 3. **图（或流程）长什么样**：节点、分支、有没有循环（ReAct 环）？
> 4. **模型调在哪**：几个调用点？各自用什么提示词、结构化还是自由文本？
> 5. **工具怎么接**：工具定义（schema/描述）、执行引擎（校验/超时/重试/权限）？
> 6. **上下文怎么管**：全量历史还是滑窗/摘要？token 预算怎么算？
> 7. **中断与人工**：有没有 interrupt/确认流？resume 值怎么校验？
> 8. **兜底链**：模型失败/工具失败/证据不足分别怎么处理？错误有没有落池？
> 9. **并发模型**：哪里 create_task、哪里 gather、有没有同步调用堵事件循环？
> 10. **可观测**：每轮 trace 记录了什么？怎么从日志复现一次完整决策？

## 结语与练习建议

到这里，全项目的主干已经被你走完一遍。给你三个动手练习（按难度排序）：

1. **加日志任务**：在 `agent_tools` 里给每个工具调用加一行耗时日志——练习读 State、改节点；
2. **加一个新意图分支**：在 `INTENT_TO_ROUTE` 加一个出口（比如「催发货」→ business），
   跑 `make eval-ch06` 看路由是否生效——练习路由与条件边；
3. **写一个后台任务**：仿照 `summarizer.maybe_schedule_summary` 的防抖 + done 回调模式，
   写一个「每轮结束异步统计 token 用量」的任务——练习第 2 篇的异步模式。

回顾整个知识库：第 1 篇过了语法关，第 2 篇过了异步关，第 3、4 篇立了 LangChain/LangGraph
的骨架，本篇把骨架填上了血肉。**你现在拥有的不是「读懂了一个项目」，
而是一张能挂任何 AI 应用代码的挂毯**——下一课见。
