# 学习知识库 — 写给 Java 开发者的 Python + AI 应用开发指南

> 本知识库专为「6 年 Java 经验、Python 和 AI 应用开发零基础」的读者编写。
> 所有讲解都用 Java 概念类比，注解统一以 **AI解析** 开头，用大白话说清楚。

## 这套文档是什么

本项目是一个电商智能客服 Agent（AI 客服机器人），技术栈是
**FastAPI + LangGraph/LangChain + MySQL + Milvus**。

它麻雀虽小五脏俱全：一个 HTTP 服务、一张「状态机」流程图、一个能查订单/查物流/答政策/走退款/建工单的
AI Agent、不断滚动摘要的上下文管理、后台跑任务的异步编程……你想学的 Python 语法、异步、多线程、
LangChain、LangGraph，项目里全都有真实用例。

本套文档教你「读懂并写出这样的项目」，而不是泛泛讲语法。

## 文档地图（建议按顺序读）

| 篇 | 文件 | 回答什么问题 | 对应 Java 知识 |
| - | - | - | - |
| 0 | 本篇 | 怎么用这套文档、项目全景 | - |
| 1 | [01-python-vs-java.md](01-python-vs-java.md) | Python 语法和 Java 有什么不一样 | 语言基础 |
| 2 | [02-async-and-threads.md](02-async-and-threads.md) | async/await 是什么？多线程怎么用 | 线程池、CompletableFuture、Netty |
| 3 | [03-langchain-essentials.md](03-langchain-essentials.md) | LangChain 是什么、怎么用 | 函数式组合、接口封装 |
| 4 | [04-langgraph-essentials.md](04-langgraph-essentials.md) | LangGraph 是什么、怎么用 | 状态机、工作流引擎 |
| 5 | [05-source-reading-tour.md](05-source-reading-tour.md) | 逐文件带读本项目源码 | 代码走读 |
| 6 | [06-context-cache-placement.md](06-context-cache-placement.md) | 摘要/半压/全压怎么放、稳定度排序 | 缓存设计、查询优化 |

## 项目全景图

先把整个项目的地图看一遍，后面读代码就不会迷路：

```
用户浏览器
   │  POST /api/chat（SSE 流式）
   ▼
app/api/chat.py          ← HTTP 入口层（类比 Spring @RestController）
   │
   ▼
app/graph/runtime.py     ← 编排层：构建输入、调图、把图的输出转成 SSE 事件
   │
   ▼
app/graph/build.py       ← 图的组装（类比状态机定义）
   │  节点是函数，边是跳转规则
   ▼
app/graph/nodes.py       ← 每个节点干啥（消解指代 → 判意图 → 分流 → 检索/工具/回复）
   │
   ├─→ app/core/*        ← 单点能力：LLM 调用、检索、重排、意图、摘要、置信度
   ├─→ app/tools/*       ← 工具系统：内置工具、MCP 工具、统一执行引擎
   └─→ app/db/*          ← 仓储层（类比 MyBatis Mapper / JPA Repository）
```

一次用户消息的旅程（简化版）：

```
"运费怎么算" 
  → resolve_reference（指代消解：把"那它呢"这种半截话补全）
  → classify_intent（意图识别：商品咨询）
  → retrieve_knowledge（检索 + 置信度闸）
  → main_agent（大模型带着工具生成回答，可能循环调用工具）
  → log（落库留痕）→ END
```

## Java 听众第一课：先破除三个思维定势

> **AI解析（1）Python 没有「编译期」这道保护网。**
> Java 里写错类型，IDE 和 javac 当场报错。Python 是动态类型语言，`a = "1" + 1` 要到运行时才炸。
> 所以这个项目全程用 **类型注解 + Pydantic 校验** 来模拟 Java 的类型安全。读代码时你会看到大量
> `str | None`、`list[dict]` 这样的写法——它们运行时不起作用（不强制），但配合 IDE 检查非常有用。
> 类比：Java 是「编译时检查 + 运行时也检查」，Python 是「不检查，但你自愿写注解让工具检查」。

> **AI解析（2）异步不等于多线程。**
> Java 里并发的主力是「开线程」；Python 里因为有 GIL（全局解释器锁，第 2 篇详解），
> 主力是 **单线程 + 事件循环的 async/await**——用「切换任务」代替「开线程」。
> Spring MVC 一个请求一个线程，FastAPI 一个事件循环里塞满并发请求。

> **AI解析（3）LangChain 和 LangGraph 不是新语言，是「库」。**
> 它们本质上是把「调用大模型的常见套路」封装成 Java 风格的组件：
> LangChain ≈ 把 LLM 调用封装成一个个可组合的「Runnable」（类比 CompletableFuture 链、Stream 流）；
> LangGraph ≈ 一个轻量工作流引擎（类比 Spring StateMachine / Activiti），
> 状态（State）在节点（Node）之间流转，边（Edge）决定下一步去哪。

## 项目关键文件速查

读源码时最常打开的 8 个文件，按重要性排序：

| 文件 | 一句话职责 | 学习焦点 |
| - | - | - |
| `app/graph/state.py` | 定义流程中流转的「状态对象」 | TypedDict、类型注解、reducer |
| `app/graph/build.py` | 组装流程图：加节点、连边 | LangGraph 核心 API |
| `app/graph/nodes.py` | 12 个节点的具体逻辑 | async 函数、图与业务的结合 |
| `app/graph/runtime.py` | 图的「运行时」：编译、流式执行 | 异步流、interrupt 续跑 |
| `app/core/llm.py` | 封装上游大模型客户端 | LangChain ChatOpenAI、结构化输出 |
| `app/core/summarizer.py` | 后台摘要任务 | asyncio.create_task、防抖 |
| `app/kb/milvus_client.py` | 向量库调用 | 单线程执行器、run_in_executor |
| `app/tools/engine.py` | 工具统一执行引擎 | 超时、重试、权限门 |

## 怎么学最快

1. **先读第 1、2 篇**，把 Python 语法和异步模型这两个「语言关」过了；
2. **再读第 3、4 篇**，理解 LangChain/LangGraph 的设计哲学（别背 API，先立骨架）；
3. **最后读第 5 篇**，对照真实源码逐文件走一遍，把前两关的知识落到具体代码上；
4. **第 6 篇是补充篇**：对「前缀缓存怎么命中、摘要/半压/全压放在哪」没看懂时，回来看它（含完整稳定度排序表）；
5. 边读边把项目跑起来（`make dev`），对照日志 `log/app.log` 观察每个节点的执行。

每篇文档中的代码片段都来自本项目真实源码（标注了文件路径），
注解统一以 **AI解析** 开头并给出 Java 类比。
