# 第 4 篇：LangGraph 核心 — 一个跑在 Python 里的「工作流引擎」

> LangGraph 是本项目真正的骨架：所有业务逻辑都是一张图上的节点。
> 本篇把这张图拆开揉碎讲清楚。注解以 **AI解析** 开头。

## 1. LangGraph 是什么：状态机 + 工作流引擎

> **AI解析（立骨架）**：传统聊天机器人是「一问一答」；而 Agent 需要
> 「判断意图 → 分流 → 检索/调用工具 → 可能循环 → 生成回答」这种**多步骤、有分支、会循环**的流程。
> 用一堆 `if/else` 硬写会变成面条代码。LangGraph 的答案：
> **把流程画成一张图（Graph），状态（State）在上面流动。**
>
> Java 类比三连：
> - ≈ **Spring StateMachine**：状态机 + 迁移规则；
> - ≈ **Activiti/Flowable 工作流引擎**：节点、连线、流程实例持久化、人工任务中断（interrupt）；
> - ≈ **带循环的管道**：比 LCEL 线性管道多了「分支」和「回头」的能力。
>
> 一张图由四样东西组成（马上逐个看本项目代码）：
> 1. **State（状态）**：流动的数据包（本项目是 `ConversationState`）；
> 2. **Node（节点）**：干活的函数，读 State、返回增量更新；
> 3. **Edge（边）**：节点间的跳转规则（固定边 / 条件边）；
> 4. **Checkpointer（检查点）**：把状态持久化，支持断点、续跑、多轮记忆。

## 2. State：不是「全局变量」，是「带合并规则的共享黑板」

```python
# app/graph/state.py（原样摘录）
from typing import Annotated
from typing_extensions import TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_dict(a: dict | None, b: dict | None) -> dict:
    """trace 累加 reducer:后写覆盖同键,其余合并;b 为 None 是重置哨兵。..."""
    if b is None:
        return {}
    return {**(a or {}), **b}


class ConversationState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # 跨轮历史,checkpointer 续接
    summary: str            # ch07 早期轮次滚动摘要
    user_id: str
    conversation_id: int
    intent: str             # 九类之一
    resolved_query: str     # 指代消解+改写后的完整问句
    order_id: str           # refund_flow:抽到/点选回填的订单号
    evidence: str           # 知识路编号证据文本
    ...
    trace: Annotated[dict, merge_dict]  # 留痕
```

> **AI解析（最重要的概念：Reducer）**：
> State 是 TypedDict（第 1 篇讲的「带类型的 dict」），所有节点共享同一份。
> 关键机制在 `Annotated[类型, 合并函数]`：**当新旧两份 state 要合并时，用哪个函数合并**。
> - `messages: Annotated[list[AnyMessage], add_messages]`：新消息**追加**到旧列表（不是覆盖），
>   所以对话历史只增不减；
> - `trace: Annotated[dict, merge_dict]`：字典按 key 合并（后写覆盖同键）；
> - **没标 Annotated 的字段用默认规则：新值覆盖旧值**。
>
> 为什么重要？因为节点的返回值是**增量**：`main_agent` 返回 `{"messages": [ai], "steps": 1}`，
> 图引擎会把它和旧 State **合并**（messages 追加、steps 覆盖）。
> 等价 Java：你更新数据库不是重写整行，而是 `UPDATE ... SET steps = 1`——
> **reducer 决定这行 UPDATE 是覆盖还是累加**。
>
> `merge_dict` 里还有个精妙设计：`b is None 是重置哨兵`——入口每轮传 `trace=None` 重置上一轮的
> 留痕。为什么不能传空字典 `{}`？注释写明了：`merge(旧, {}) = 旧`——空字典合并等于没传，
> 清不掉旧键。**用 None 当「重置」信号，是 reducer 设计里的一个必备逃生口**。
>
> Java 类比彩蛋：这套「增量更新 + 合并规则」和**事件溯源（Event Sourcing）**、
> **CRDT 合并**是同一思想家族：状态 = 历史事件的折叠（fold）。

## 3. 组装图：add_node / add_edge / add_conditional_edges

```python
# app/graph/build.py（完整原样摘录）
from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.routing import confidence_gate, route_by_intent, should_continue
from app.graph.state import ConversationState


def _builder() -> StateGraph:
    b = StateGraph(ConversationState)
    # 节点
    b.add_node("resolve_reference", nodes.resolve_reference)
    b.add_node("classify_intent", nodes.classify_intent)
    b.add_node("retrieve_knowledge", nodes.retrieve_knowledge)
    b.add_node("confidence_check", nodes.confidence_check)
    b.add_node("main_agent", nodes.main_agent)
    b.add_node("agent_tools", nodes.agent_tools)
    b.add_node("complaint_reply", nodes.complaint_reply)
    b.add_node("script_reply", nodes.script_reply)
    b.add_node("fallback_reply", nodes.fallback_reply)
    b.add_node("fetch_order", nodes.fetch_order)
    b.add_node("retrieve_policy", nodes.retrieve_policy)
    b.add_node("log", nodes.log_node)

    # 骨架:消解 → 意图 → 分流
    b.add_edge(START, "resolve_reference")
    b.add_edge("resolve_reference", "classify_intent")
    b.add_conditional_edges("classify_intent", route_by_intent, {
        "escalate": "complaint_reply",
        "fallback_script": "script_reply",
        "knowledge": "retrieve_knowledge",
        "refund_flow": "fetch_order",
        "business": "main_agent",
    })
    # 退款子流程确定性链:取单 → 检索政策 → 交主力 Agent 判能不能退
    b.add_edge("fetch_order", "retrieve_policy")
    b.add_edge("retrieve_policy", "main_agent")
    # 知识路:检索 → 生成前证据闸
    b.add_edge("retrieve_knowledge", "confidence_check")
    b.add_conditional_edges("confidence_check", confidence_gate, {
        "strong": "main_agent",
        "weak": "fallback_reply",
    })
    # 主力 Agent ReAct 环
    b.add_conditional_edges("main_agent", should_continue, {
        "continue": "agent_tools",
        "stop": "log",
    })
    b.add_edge("agent_tools", "main_agent")
    # 确定性出口 → 日志 → END
    b.add_edge("complaint_reply", "log")
    b.add_edge("script_reply", "log")
    b.add_edge("fallback_reply", "log")
    b.add_edge("log", END)
    return b


def build_graph(checkpointer=None):
    return _builder().compile(checkpointer=checkpointer)
```

> **AI解析（逐层拆）**：
> 1. `StateGraph(ConversationState)`：创建一张「以 ConversationState 为状态类型」的图。
>    等价 Java：`new StateMachineBuilder<ConversationState>()`。
> 2. `add_node("名字", 函数)`：注册节点。**节点就是普通函数（本项目全是 async 函数）**，
>    签名约定：入参是完整 state，返回值是「要合并进 state 的增量 dict」。
>    等价 Java：`StateMachineBuilder.state("名字").onEntry(state -> {...})`。
> 3. `add_edge(A, B)`：**固定边**——A 执行完必去 B。等价状态机的无条件迁移。
> 4. `add_conditional_edges(源节点, 路由函数, 映射表)`：**条件边**——源节点执行完后，
>    调用「路由函数」拿一个字符串，按映射表决定下一站。等价：
>    ```java
>    switch (router(state)) {        // 路由函数返回 "knowledge"/"business"/...
>        case "knowledge" -> next("retrieve_knowledge");
>        case "business"  -> next("main_agent");
>        ...
>    }
>    ```
>    路由函数本身只是普通函数（读 state 返回字符串），**业务判断和图结构解耦**——
>    这是在 `routing.py` 里单独放的原因。
> 5. `START` / `END`：图的入口和出口常量（不用自己定义）。
> 6. `.compile(checkpointer=...)`：编译成可执行图。等价「把 BPMN 流程图部署到引擎」。

## 4. 路由函数：图上的「交通警察」

```python
# app/graph/routing.py（原样摘录）
INTENT_TO_ROUTE: dict[str, str] = {
    "投诉": "escalate",
    "闲聊": "fallback_script",
    "其他": "fallback_script",
    "商品咨询": "knowledge",
    "退款退货": "refund_flow",
    "售后": "refund_flow",
    "人工": "business",   # ch08:明确要求建工单/转人工
    "物流": "business",
    "订单": "business",
}


def route_by_intent(state) -> str:
    """按意图分流;未知意图保守归 business(让 Agent 自己应对)。"""
    return INTENT_TO_ROUTE.get(state.get("intent", ""), "business")


def should_continue(state) -> str:
    """ReAct 停止条件:无 tool_calls 就收敛,步数封顶则强停,否则继续。..."""
    last = state["messages"][-1]
    has_tool_calls = isinstance(last, AIMessage) and bool(last.tool_calls)
    if not has_tool_calls:
        return "stop"
    if state.get("steps", 0) >= settings.max_agent_steps:
        return "stop"
    return "continue"
```

> **AI解析**：
> - `route_by_intent`：一张「意图 → 出口」的查表。用 `dict.get(键, 默认值)` 兜底——
>   未知意图落 `business`（交给主力 Agent 兜底），**永不抛异常**。
>   注意注释：路由表是「单一来源」——`classify_intent` 节点也用它算 `route` 字段，
>   保证「图的路由」和「日志里的 route」永不漂移。这是配置集中化的典型做法。
> - `should_continue`：ReAct 环的出口判断——**这就是「Agent 什么时候停下」的答案**：
>   ① 模型最后一条消息没有 tool_calls（它决定直接回答用户了）→ stop；
>   ② 步数到上限（防死循环保险丝）→ 强停；
>   ③ 否则 → continue，再走一轮「执行工具 → 再问模型」。
>   注释里还有一段很棒的工程思考：「防打转靠步数而不是 token」——一步一次模型调用，
>   数得清、好解释；token 是成本问题，不属于循环终止条件的职责。

## 5. 节点函数：读 State → 干活 → 返回增量

```python
# app/graph/nodes.py（classify_intent 原样摘录）
async def classify_intent(state) -> dict:
    """意图四件套:八类 + confidence + 「其他」兜底。..."""
    query = state.get("resolved_query") or _user_text(state)
    r = await intent_mod.classify(query, _history_text(state))
    intent, conf = r["intent"], r["confidence"]
    route = INTENT_TO_ROUTE.get(intent, "business")
    tag_intent(intent, conf)   # ch09:意图进当前 trace 的 metadata+tag
    return {"intent": intent, "intent_confidence": conf, "route": route,
            "trace": {"intent": intent, "intent_confidence": conf, "route": route}}
```

> **AI解析（节点函数的统一套路）**：
> 1. **入参**：完整 state（用 `.get()` 安全取值，因为 TypedDict total=False 下键可能不存在）；
> 2. **干活**：调用 core 层能力（`intent_mod.classify` 就是第 3 篇讲的结构化输出）；
> 3. **返回增量**：只返回「我改了哪些键」，**不返回整个 state**——图引擎负责合并
>    （有 reducer 的追加/合并，没 reducer 的覆盖）。
> 4. `or` 短路：`state.get("resolved_query") or _user_text(state)`——
>    空字符串是「假值」，所以这句等于「resolved_query 为空就用原话」，等价 Java
>    `StringUtils.isNotBlank(q) ? q : fallback()`。
>
> **节点 + 条件边 = 完整的「业务分支」**：节点算出 `intent`；路由函数把 `intent` 翻译成出口；
> 映射表决定去哪。三段职责分离，每一段都可单独测试（项目 tests/ 里就是分开测的）。

## 6. ReAct 环：Agent 的心脏

「ReAct」= Reasoning + Acting：模型**想一步 → 调工具 → 看结果 → 再想 → ... → 出答复**。
本项目的实现就两个节点 + 一个条件边：

```python
# app/graph/nodes.py（main_agent 原样摘录,节选）
async def main_agent(state, config=None) -> dict:
    """ReAct 推理步:调模型(带工具),累加 steps 与 token 消耗。..."""
    specs = await registry.get_all_specs()          # 现问现拿:内置+MCP 合并清单
    model = get_chat_model(streaming=True).bind_tools([s.tool for s in specs])
    msgs = _agent_messages(state)
    _log_model_context(state, msgs)
    ai: AIMessage = await model.ainvoke(msgs, config)
    usage = ai.usage_metadata or {}
    used = usage.get("total_tokens", 0)
    ...
    return {"messages": [ai],
            "steps": state.get("steps", 0) + 1,
            "tokens_used": state.get("tokens_used", 0) + used}
```

```python
# app/graph/nodes.py（agent_tools 原样摘录,节选）
async def agent_tools(state) -> dict:
    """ReAct 行动步(ch08):一切工具经统一执行引擎。..."""
    last = state["messages"][-1]
    ...
    for tc in last.tool_calls:
        ...
        run = await engine.execute_tool_call(tc, cid, specs, user_id=uid)
        tool_msgs.append(run.tool_message)
    out = {"messages": tool_msgs}
    if actions:
        out["suggested_actions"] = actions
    return out
```

> **AI解析（环怎么转起来的）**：
> 1. `main_agent` 调模型时用 `bind_tools([...])` **把工具清单「绑」给模型**：
>    请求里带上每个工具的 JSON Schema，模型如果决定用工具，回复里的
>    `AIMessage.tool_calls` 就有内容（例如 `[{"name": "query_order", "args": {"order_id": "1001"}}]`）。
>    注意 `bind_tools` 返回的是**新的模型对象**（不可变链式风格，类似 Java 的 builder），
>    每次现拿工具清单再 bind——因为 MCP 工具可能动态变化（`现问现拿`）。
> 2. `main_agent` 的返回值里 `"messages": [ai]`——reducer `add_messages` 会把它**追加**到历史；
>    `"steps": 旧值 + 1`——手动累加（数字字段是「覆盖」规则，所以要在节点里自己加）。
> 3. 条件边 `should_continue` 看到「最后一条 AI 消息有 tool_calls」→ 去 `agent_tools`；
> 4. `agent_tools` 从 `last.tool_calls` 里逐个取出调用，交给统一执行引擎执行，
>    把结果包装成 `ToolMessage` 列表返回（又被追加进历史）；
> 5. **固定边 `agent_tools → main_agent`** 把流程送回起点：
>    这次模型看到的历史里多了「工具结果」，于是继续推理——或者给最终答复（无 tool_calls → stop）。
>
> 整个环的形状：`main_agent →(有 tool_calls)→ agent_tools → main_agent → ... →(无 tool_calls)→ log`。
> **这就是所有 Agent 框架（ReAct paper 的工程实现）的核心循环**，
> 你在别的框架（LangChain AgentExecutor、OpenAI Assistants）会看到同样的结构。
>
> 安全细节（图结构层面的防御）：`submit_refund` 工具被 `agent_tools` **拦截成前端表单**，
> 根本不到执行引擎；`create_ticket`（唯一写操作）在节点顶部 `interrupt` 弹确认卡——
> **「写操作必须由用户确认」是图结构保证的，不是靠提示词恳求模型遵守**。

## 7. interrupt / resume：工作流引擎的「人工节点」

有些步骤必须「暂停等用户」：退款要先选订单、建工单要先确认。
LangGraph 的 `interrupt()` 就是工作流引擎里的「人工任务（User Task）」：

```python
# app/graph/nodes.py（fetch_order 原样摘录）
async def fetch_order(state) -> dict:
    """退款子流程第一步:抽订单号;缺了、或者报的不是自己的单,都 interrupt 弹订单选择器
    等前端点选(resume 回填);拿到后 order_snapshot 取订单数据。
    interrupt 之前只做只读(resume 时本节点从头重跑)。..."""
    uid = state.get("user_id", "")
    oid = state.get("order_id") or _extract_order_id(
        state.get("resolved_query") or _user_text(state))
    while not business.owns_order(uid, str(oid or "")):
        orders = business.list_user_orders(uid)                        # 只读,可安全重跑
        oid = interrupt({"type": "select_order", "orders": orders})    # resume 回填订单号
    data = business.order_snapshot(oid)
    return {"order_id": oid, "order_data": data,
            "trace": {"fetch_order": {"order_id": oid}}}
```

> **AI解析（interrupt 的机制，划重点）**：
> 1. `oid = interrupt({...})`：执行到这里图**当场暂停**，把 payload（订单列表）交给调用方，
>    状态被 checkpointer 持久化——进程重启都不怕。等价 Activiti 的「等待人工任务」。
> 2. 前端展示订单选择器，用户点选后调用 resume 接口，用 `Command(resume=选中的订单号)` **续跑**：
>    `interrupt(...)` 这个调用**返回**了 resume 传进来的值（即前面那个 `oid = ` 赋值成立）。
>    等价 Java 工作流引擎的 `runtimeService.signal(executionId, variables)`。
> 3. **生死规则：「resume 时本节点从头重跑」**——interrupt 不是「暂停在这一行继续」，
>    而是「整个节点重新执行，interrupt() 直接返回续跑值」。所以：
>    - `interrupt` 之前**只能做只读操作**（这里 `list_user_orders` 是只读，注释还专门标了）；
>    - 如果要 resume 后干写操作，必须放在 interrupt 之后（或幂等设计）。
>    这等价「工作流节点重试必须幂等」的经典工程约束。**读任何 interrupt 代码，
>    第一眼就找「重跑安全边界」在哪。**
> 4. `while` 循环：用户选的订单也得过 `owns_order` 校验——**resume 回传的数据同样是不可信输入**，
>    如果选了个不属于自己的单，继续弹选择器。这类「信任边界不因中断续跑而放松」的细节，
>    是生产级代码和玩具代码的分水岭。
>
> `main_agent`/`agent_tools` 里的 `create_ticket` 确认卡（`interrupt({"type": "confirm_ticket", "preview": ...})`）
> 用的是同一套机制：参数齐 → 弹预览卡 → 用户确认 → resume 回 `{"confirmed": True}` → 真执行。
> **「写操作永远经过 interrupt 确认」在项目里是结构性设计，值得照抄。**

## 8. Checkpointer：多轮记忆 + 断点续跑的底座

```python
# app/graph/runtime.py（init_graph 原样摘录）
async def init_graph() -> None:
    """lifespan 起:开持久 checkpointer + setup + 编译图。"""
    global _graph, _cm
    _cm = AsyncSqliteSaver.from_conn_string(settings.checkpointer_db_path)
    checkpointer = await _cm.__aenter__()
    await checkpointer.setup()
    _graph = attach_observability(build_graph(checkpointer=checkpointer))
```

```python
# app/graph/runtime.py（调用时的 config）
config = {"configurable": {"thread_id": str(cid)},
          "metadata": {"langfuse_session_id": str(cid)}}
final = await get_graph().ainvoke(_graph_input(...), config)
```

> **AI解析**：
> 1. `AsyncSqliteSaver`：**用 SQLite 文件做持久化的检查点存储**。每一步执行后，
>    图把整个 State 存一次（带版本）。等价工作流引擎的「流程实例表 + 变量表」。
>    带 `Async` 前缀 = async 版本（不阻塞事件循环）。
> 2. `from_conn_string(...)` 返回的是「异步上下文管理器」而非直接的对象——
>    所以 `init_graph` 里用 `await _cm.__aenter__()` 手动进入、把 `_cm` 存成全局变量防 GC，
>    关闭时再 `__aexit__`。**为什么不能直接 `async with`？** 因为应用的生命周期
>    （FastAPI lifespan：启动 → 服务多年 → 关闭）和 `async with` 的代码块
>    （进出在同一个函数里）不匹配——连接要活过整个进程，只能手动管理进出。
>    Java 类比：`DataSource` 在 `@PostConstruct` 里建、`@PreDestroy` 里关。
> 3. `thread_id`：**会话标识**。checkpointer 按 thread_id 分组存取 State——
>    同一个 conversation_id 的每一轮都读写同一份持久化状态，
>    **于是「多轮记忆」和「interrupt 后隔几分钟还能续跑」都白拿了**。
>    Java 类比：流程实例 ID。注意本项目 `thread_id = str(conversation_id)`，
>    一个会话一条状态线。
> 4. 这也解释了为什么 State 里 `messages` 能「跨轮历史」：每轮 append 的消息都被
>    checkpointer 持久化，下一轮再 `ainvoke` 时图先把旧 State 载回来、再合并新一轮输入。

## 9. 执行方式：invoke / ainvoke / astream

```python
# app/graph/runtime.py（三种调用入口,摘录）
# ① 非流式:一次跑完拿终态（给内部 API / 测试用）
final = await get_graph().ainvoke(_graph_input(...), config)

# ② 非流式续跑:从 interrupt 处回填继续
final = await get_graph().ainvoke(Command(resume=resume_value), config)

# ③ 流式:边跑边吐事件（给前端 SSE 用）
async for mode, chunk in get_graph().astream(
    stream_source, config, stream_mode=["messages", "updates"],
):
    if mode == "messages":
        msg, meta = chunk          # 模型吐的 token 流 + 来源节点信息
        ...
    elif mode == "updates":
        for node, upd in chunk.items():   # 每个节点跑完后的增量更新
            ...
```

> **AI解析**：
> - `ainvoke` = 同步语义的「调用并等终态」，返回最终 State。等价 `processEngine.startProcessInstance(...)` 跑完整流程。
> - `astream(..., stream_mode=["messages", "updates"])` = **多模式流**——
>   LangGraph 可以同时吐多种事件流：
>   - `"messages"` 模式：**模型生成的 token 流**（打字机效果的数据源），
>     每块带 `langgraph_node` 元信息（来自哪个节点，项目只透传 `main_agent` 的）；
>   - `"updates"` 模式：**每个节点完成后的增量更新**（项目用它转发「引用」「工具调用」「中断」事件）；
>   多模式让一个流同时满足「正文打字机」+「流程状态通知」两个需求，
>   前端拿到的是一锅「事件汤」，按 `type` 分发即可（`chat.py` 就是干这个的）。
> - `Command(resume=...)`：续跑指令对象。等价「对暂停的流程实例发信号」。
>   注意 resume 走的还是同一个 `astream/ainvoke`，只是输入从「新消息」换成「恢复指令」。

## 10. 全图复盘：把 12 个节点串成一个故事

现在把 build.py 的图用「用户视角的故事」重讲一遍（对照第 0 篇的简图）：

```
START
 └─ resolve_reference   消解指代:「那它呢」→「猫粮的运费怎么算」
     └─ classify_intent 判意图:九类之一 + 置信度
         ├─(投诉)→ complaint_reply   安抚话术 + 转人工/建单按钮 → log
         ├─(闲聊/其他)→ script_reply  脚本话术(零模型) → log
         ├─(商品咨询)→ retrieve_knowledge  混合检索+置信度闸
         │               └─ confidence_check ─(强)→ main_agent
         │                                   └─(弱)→ fallback_reply(落低置信池) → log
         ├─(退款退货/售后)→ fetch_order   抽单号/弹选择器(interrupt)
         │                    └─ retrieve_policy  强制检索退款政策
         │                        └─ main_agent   带着政策证据+订单数据判「能不能退」
         └─(物流/订单/人工)→ main_agent   ← ReAct 环入口
                 │  main_agent ⇄ agent_tools   (带工具调模型 ↔ 执行工具喂回结果)
                 └─(无 tool_calls 或步数到顶)→ log → END
```

> **AI解析（设计亮点）**：
> 1. **确定性优先**：能不用模型就不用——投诉/闲聊走固定话术（`script_reply` 零模型调用），
>    既快又稳又省钱。**模型只留给真正需要理解力的环节**（意图判断、检索问答、工具决策）。
> 2. **强制检索（Forced RAG）**：知识路和退款路都强制先检索再把证据喂给模型，
>    模型不能「凭记忆答」（README 里那句「不让模型凭记忆答」）。
>    生成前还有两道闸：数值置信度（四信号加权）和语义自评（`self_check`）。
> 3. **兜底思维贯彻全图**：未知意图 → business（Agent 兜底）；证据弱 → fallback_reply
>    （兜底话术 + 帮用户转人工）；工具炸了 → 错误如实回灌模型让它向用户解释。
>    **每条路都有归处，没有「走到死胡同」的分支。**
> 4. **失败也是数据**：`fallback_reply` 把答不上的问题落「低置信池」，
>    后面第 9 章的「数据飞轮」从池子里捞出来人工审核→补进知识库。
>    失败路径不是垃圾桶，是**生产管线的一部分**。

## 11. LangGraph 心智模型速记（背这张表）

| LangGraph 概念 | 一句话 | Java 类比 |
| - | - | - |
| `StateGraph(State)` | 以 State 为数据载体的图 | `StateMachineBuilder<T>` |
| `State`（TypedDict） | 流动的共享数据包 | 流程变量表 |
| `Annotated[..., reducer]` | 该字段的合并规则 | `UPDATE` 语义 / 事件折叠 |
| `add_node(name, fn)` | 注册节点 | `@Service` + 状态注册 |
| `add_edge(A, B)` | 固定跳转 | 无条件迁移 |
| `add_conditional_edges(n, router, map)` | 按路由函数分支 | `switch` 决策网关 |
| `START` / `END` | 入口/出口 | 流程起止节点 |
| `checkpointer` + `thread_id` | 状态持久化/多轮记忆/断点 | 流程实例表 |
| `interrupt` / `Command(resume)` | 暂停等用户，回填续跑 | 人工任务 + `signal()` |
| `ainvoke` / `astream` | 跑完拿终态 / 边跑边吐事件 | `startProcess` / 事件订阅 |

下一篇（第 5 篇）：**源码带读**——按文件顺序把本项目走一遍，
把前四篇的知识点全部落到真实代码上。
