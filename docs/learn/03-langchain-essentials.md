# 第 3 篇：LangChain 核心 — 把「调用大模型」封装成 Java 风格的组件

> LangChain 是 AI 应用开发的地基库。本篇不求全，只讲本项目用到的部分：
> 消息模型、模型客户端、提示模板、LCEL 管道、结构化输出、工具定义。
> 注解以 **AI解析** 开头。

## 1. LangChain 到底解决什么问题

> **AI解析（先立心智模型）**：假设没有 LangChain，你要调用某家的大模型 API，得自己：
> 拼 JSON 请求、处理鉴权、解析响应、处理流式 chunk、重试、统计 token、格式化历史消息……
> 而且每换一家供应商（OpenAI、DeepSeek、通义、MiniMax）协议细节都有小差异。
>
> LangChain 做的事 = **JDBC 对数据库做的事**：
> - `ChatOpenAI` 就像一个「驱动」，把各家大模型统一成同一套 Java 风格接口；
> - 消息类型（HumanMessage/AIMessage...）就像 JDBC 的 ResultSet/PreparedStatement——
>   一套标准模型，屏蔽底层差异；
> - LCEL 管道（`prompt | model`）就像 Stream API / CompletableFuture 链——把「模板→调模型→解析」
>   串成一条声明式流水线；
> - `@tool`、`structured output` 是把 LLM 能力接入普通代码的「适配器」。
>
> 一句话：**LangChain = LLM 应用的 Spring**（统一封装 + 约定优于配置 + 可组合组件）。

## 2. 消息模型：AI 世界的「HTTP 协议」

大模型 API 的本质是「发一串消息，回一串消息（或流）」。LangChain 把消息定义成四个类型，
本项目每个都用到了：

| 消息类 | 谁说的 | Java 类比 |
| - | - | - |
| `SystemMessage` | 系统提示（人设、规则） | 配置文件/全局常量（不给用户看） |
| `HumanMessage` | 用户 | 请求 DTO |
| `AIMessage` | 模型 | 响应 DTO（可能带 tool_calls） |
| `ToolMessage` | 工具执行结果 | RPC 调用返回体 |

```python
# app/graph/state.py
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

class ConversationState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # 跨轮历史,checkpointer 续接
```

> **AI解析**：整个会话历史就是 `List<AnyMessage>`（AnyMessage = 四类的父类型联合）。
> 每轮对话往这个列表里追加消息，调模型时把整个列表发给它——**大模型没有服务端会话记忆，
> 「多轮对话」全靠客户端把历史重新发一遍**。这是 AI 应用和传统 Web 最大的不同之一：
> 状态在客户端（我们这个项目在 checkpointer 里）。
> `add_messages` 是 LangGraph 的「reducer」（第 4 篇详解），负责合并新旧消息列表。

## 3. ChatOpenAI：模型客户端（一个类打通所有供应商）

```python
# app/core/llm.py
from langchain_openai import ChatOpenAI

class _CumulativeUsageChatOpenAI(ChatOpenAI):
    """修正流式用量被重复累加的问题。..."""

def get_chat_model(streaming: bool = False, model: str | None = None,
                   temperature: float | None = None, slot: str = "chat",
                   thinking: bool = True) -> ChatOpenAI:
    slot_model, base_url, api_key = resolve_slot(slot)
    ...
    return _CumulativeUsageChatOpenAI(
        model=model or slot_model,
        base_url=base_url,
        api_key=api_key,
        streaming=streaming,
        stream_usage=True,
        max_tokens=settings.max_output_tokens,
        temperature=0.3 if temperature is None else temperature,
        **thinking_kw,
    )
```

> **AI解析（逐点拆）**：
> 1. `ChatOpenAI` 是 LangChain 对「OpenAI 协议」的封装。目前国内国外绝大多数大模型
>    （DeepSeek、通义、MiniMax、硅基流动、Kimi……）都提供「OpenAI 兼容接口」——
>    协议一样，只是 `base_url` 和 `api_key` 不同。所以**一个 ChatOpenAI 类就能连所有供应商**，
>    和 JDBC 用一套接口连所有数据库完全同构。项目里 `.env` 换模型只需改配置，代码不动。
> 2. `model` / `temperature` / `max_tokens` 是通用参数：
>    - `temperature` 是「随机性旋钮」：0 = 每次回答尽量一样（当评委用），
>      0.3 = 有点人味（答用户用）。项目注释里那句「当评委用的链路要显式传 0」就是这意思。
>    - `max_tokens` = 单次回答的长度上限（保险丝）。
> 3. `**thinking_kw`：Python 的双星号解包——把一个 dict 摊开成关键字参数，
>    等价 Java 里 `Map<String, Object>` 展开传参（Java 没这语法，只能手动一个个传）。
> 4. `_CumulativeUsageChatOpenAI` 子类：**继承 ChatOpenAI 并重写一个受保护方法**，
>    修复上游「每个 chunk 都重报累计 token 数」导致的用量虚高 bug。
>    这是典型的「驱动层打补丁」——业务代码不用知道，换掉这个子类就行。
>    （想深挖可读 `llm.py` 的 `_diff_usage`，本质是把累计值转成增量，像滑动窗口求和。）

> **AI解析（为什么项目要自己包一层 get_chat_model）**：
> 这是「工厂函数」模式：所有业务代码不直接 new ChatOpenAI，而是统一走 `get_chat_model()`，
> 好处是「思考链开关」「槽位模型选择」「usage 上报」这些横切逻辑只写一处。
> Java 类比：`@Configuration` 里统一定义 `@Bean ChatClient`，业务代码注入用。

## 4. Prompt 模板：别把提示词当字符串拼

```python
# app/core/prompts.py
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

EXTRACT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", EXTRACT_SYSTEM),
        ("human", "{text}"),
    ]
)

AGENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", AGENT_SYSTEM),
        MessagesPlaceholder("history"),
    ]
)
```

> **AI解析**：
> - `ChatPromptTemplate.from_messages([...])`：声明「消息列表模板」。每个元组是
>   `(角色, 模板文本)`，`{text}` 是占位符——等价 Java 的 `String.format("...%s...")`
>   或 FreeMarker/Thymeleaf 模板（只是简单得多）。
> - `MessagesPlaceholder("history")`：留一个「消息列表插槽」，运行时把整个历史消息列表塞进去。
>   等价 Java 里模板引擎的「循环片段」，但专门为消息列表设计。
> - **提示词（prompt）在 AI 应用里是一等公民**：它不是日志文案，而是「给模型的程序」。
>   看项目里那些 `AGENT_SYSTEM` 的写法（角色、红线、禁止事项），本质上是在写
>   「自然语言版接口契约」。学 AI 应用开发，一半时间在学怎么写提示词。
> - 项目为什么单独建 `prompts.py` 文件？和 Java 把 SQL 抽到独立文件/常量类一个道理：
>   集中管理、方便对比迭代、避免散落在业务逻辑里。

## 5. LCEL 管道：`prompt | model` 是怎么回事

```python
# app/core/intent.py
model = llm.structured(_Intent, slot="intent")
r: _Intent = await (INTENT_CLASSIFY_PROMPT | model).ainvoke(
    {"query": query, "history": history or "(无)"})
```

```python
# app/core/coref.py
r = await (COREF_REWRITE_PROMPT | model).ainvoke(
    {"query": query, "history": history or "(无)"})
```

> **AI解析（`|` 是运算符重载）**：Python 允许类定义 `__or__` 方法重载 `|` 运算符。
> LangChain 的 `Runnable` 基类实现了它：`a | b` 返回一个新的「组合 Runnable」——
> 把 a 的输出接到 b 的输入。所以 `提示模板 | 模型` 的含义是：
> **「先把变量填进模板，再把填好的提示发给模型」**。
> Java 类比：
> ```java
> Function<Map<String,Object>, PromptValue> tpl = prompt::format;
> Function<PromptValue, AiMessage> call = model::invoke;
> var pipeline = tpl.andThen(call);          // 这就是 `|` 干的事
> pipeline.apply(Map.of("query", query));
> ```
> 只是 Java 的 `.andThen()` 写起来啰嗦，Python 的 `|` 语法更清爽。
> `ainvoke(输入)` = 异步执行整条管道（a 前缀又出现了——LangChain 每个 Runnable 都同时有
> `invoke` 和 `ainvoke` 两个版本，同步异步双接口，和 Java 里同一逻辑提供同步阻塞与
> 异步回调两个 API 一样）。

> **AI解析（为什么是「管道」哲学）**：AI 应用的典型流程是
> 「拼提示 → 调模型 → 解析输出 → 后处理」，每一段都可替换、可复用。
> LCEL 让整个应用变成**一条条声明式的流水线**，而不是命令式的步骤堆砌。
> 这和你熟知的 Java Stream `list.stream().filter().map().collect()` 是同一种设计美学：
> 组合优于继承，声明优于命令。

## 6. 结构化输出：把大模型当「接口」用（重点）

大模型天然返回自然语言（一段文本）。但很多场景你需要的不是聊天，而是**结构化数据**：
「把用户这句话分类成九类意图之一」「从描述里抽出订单号」。LangChain 的
`with_structured_output` 就是干这个的——**用 Pydantic 类定义「接口协议」，
让模型按 JSON Schema 返回对象**。

```python
# app/core/intent.py
class _Intent(BaseModel):
    intent: Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "人工", "闲聊", "其他"] = Field(
        description="九类意图之一")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="判断把握 0-1")

async def classify(query: str, history: str = "") -> dict:
    model = llm.structured(_Intent, slot="intent")
    try:
        r: _Intent = await (INTENT_CLASSIFY_PROMPT | model).ainvoke(
            {"query": query, "history": history or "(无)"})
    except Exception:
        return {"intent": "其他", "confidence": 0.0}
    intent = r.intent if r.intent in INTENTS else "其他"
    return {"intent": intent, "confidence": float(r.confidence)}
```

> **AI解析（这一步的魔法）**：`llm.structured(_Intent, ...)` 返回的不是普通模型，
> 而是一个「保证输出 _Intent 形状」的 Runnable。底层机制是 **function calling**：
> LangChain 把 `_Intent` 类转成 JSON Schema，作为一个「函数定义」偷偷发给模型，
> 告诉它「你必须以调用这个函数的形式回答」。模型于是返回
> `{"intent": "物流", "confidence": 0.9}` 而不是一段散文——
> **凭空给大模型装上了「返回 DTO」的能力**，等价于把一个只会聊天的接口变成了 RPC 接口。
>
> Java 类比：你调用一个远程服务，约定好 `IntentResponse` 的字段（= Pydantic 类），
> 服务按契约返回对象（= 结构化输出），反序列化失败要兜底（= except 分支归「其他」）。
> 区别在于对端是不确定的概率模型，所以**防御性编程比调普通 RPC 更重要**：
> 注意最后的 `except Exception: return {"intent": "其他", ...}`——解析失败绝不让流程崩，
> 落到最保守的默认值。这是 AI 应用代码的常态：**任何模型交互都必须有兜底。**

> **AI解析（项目里更深的坑，见 llm.py 注释）**：项目没直接用 LangChain 原版的
> `with_structured_output`，而是封装了自己的 `structured()`，原因很工程化（llm.py 长注释）：
> 1. 某些中转的 OpenAI 兼容层在**非流式**下会把 function call 参数拼坏
>    （`{}{"faithful":...}`），改用流式就干净——所以默认 `streaming=True`；
> 2. 模型偶尔在正文里多吐一段「思考」，解析正文的模式会拿到自然语言返回 None，
>    所以强制走 `method="function_calling"`（只认 tool_calls，不解析正文）；
> 3. 失败**原样重试一次**（偶发问题），仍失败就抛给调用点降级——**不在这一层降级到更差的通路**。
>
> 这段代码值得细读：它是「和不确定的上游打交道」的教科书——
> 每一种失败形态（抛异常 / 返回 None）都明确处理，注释里全是实测数据。
> 你未来写 AI 应用，80% 的坑都会长这个样子。

## 7. `@tool`：给模型递「遥控器」

Agent 和普通聊天机器人的本质区别：**能调用工具（查库、调 API、干实事）**。
LangChain 用 `@tool` 装饰器把普通函数变成模型可调用的工具：

```python
# app/tools/builtin/orders.py
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

@tool
async def query_order(
    order_id: Annotated[str, Field(description="订单号,例如 1001")],
    user_id: Annotated[str, InjectedToolArg],
) -> dict:
    """查询订单的状态、金额、下单时间、商品名和物流单号(tracking_no)。用于用户询问某个订单情况时。
    要查物流轨迹,需先用本工具拿到订单的 tracking_no,再把它传给 query_logistics。
    发起人身份由系统注入,你不要传 user_id。"""
    if not owns_order(user_id, order_id):
        return dict(NOT_OWNED)
    return order_snapshot(order_id)
```

> **AI解析（@tool 的三件套）**：
> 1. **函数名** = 工具名（`query_order`）；
> 2. **docstring** = 工具描述 + 使用说明——**这是写给大模型读的「接口文档」**，
>    模型靠它决定「什么时候用这个工具、怎么串工具」（比如 docstring 里教它：
>    想查物流先调 query_order 拿 tracking_no）。描述写不好，模型就用不好工具；
> 3. **参数注解** = 参数 JSON Schema——`Field(description=...)` 也是给模型的提示。
>
> `InjectedToolArg` 是个安全设计：`user_id` 标了它，意味着这个参数**不暴露给模型**，
> 由系统在执行时注入可信值。为什么？因为模型可能被用户话术诱导填假身份
> （提示注入攻击），**身份这种可信数据绝不能过模型的手**。Java 类比：
> 后端从 `SecurityContext` 拿当前用户，绝不信任前端传来的 `userId` 参数——同一个安全原则。
>
> 注意这个工具是 `async def`：LangChain 的 `@tool` 兼容同步和异步函数，
> 异步版本在事件循环里执行不阻塞（呼应第 2 篇）。
> 注册进项目自己的工具系统（`registry.register(...)`）后，模型调用它的流程是：
> 模型返回「我要调 query_order」→ 项目执行 → 结果包成 `ToolMessage` 回灌 → 模型继续说话。

## 8. RunnableLambda：把任意函数接进管道

```python
# app/core/llm.py（structured 的结尾）
from langchain_core.runnables import RunnableLambda

def _run(x):
    for attempt in (1, 2):
        try:
            r = fc.invoke(x)
        except Exception as e:
            _log(attempt, type(e).__name__)
            if attempt == 2:
                raise
            continue
        if r is not None:
            return r
        _log(attempt, "响应里没有 tool_calls")
    raise _no_tool_calls()

async def _arun(x):
    ...  # 异步版本,逻辑相同

return RunnableLambda(_run, afunc=_arun)
```

> **AI解析**：`RunnableLambda` 是「适配器」——把一个**普通函数**包装成管道组件，
> 让它能和 `prompt | model | ???` 继续组合。同时传 `func`（同步）和 `afunc`（异步）两个实现，
> LangChain 会自动选择：在 async 上下文里用 `_arun`，同步上下文用 `_run`。
> Java 类比：给 `Stream` 自定义一个中间操作，且同时实现阻塞与响应式两个版本。
> 这段 `for attempt in (1, 2)` 就是上一节说的「偶发失败原样重试一次」：
> 第一次失败 `continue` 循环，第二次失败 `raise` 抛给调用点。

## 9. usage_metadata：token 记账

```python
# app/graph/nodes.py（main_agent 节选）
ai: AIMessage = await model.ainvoke(msgs, config)
usage = ai.usage_metadata or {}
used = usage.get("total_tokens", 0)
cached = (usage.get("input_token_details") or {}).get("cache_read", 0)
```

> **AI解析**：模型每次回答都会附带 token 用量（输入多少、输出多少、缓存命中多少）。
> token 是 AI 应用的「计费单位」，也是上下文窗口的「容量单位」——就像数据库的 IOPS，
> 必须记账。`usage_metadata` 是 LangChain 统一后的用量结构；
> `cache_read`（前缀缓存命中数）是省钱的关键指标：system 提示 + 工具定义这段
> 每轮都一样的「前缀」如果被缓存复用，输入费用能打骨折价。项目后面第 7 章整章都在做
> 「上下文管理」——目的就是保缓存命中率（本项目注释里那句「实测 cache_read=2048，
> 摘要一进 system 就掉到 0」就是在讲这个）。

## 10. LangChain 学习地图（本项目用到 vs 没用到）

本项目只用了 LangChain 的**核心原语**（这是刻意的：课程教你地基而非全家桶）：

| 用到 | 位置 | 没用到（了解即可） |
| - | - | - |
| ChatOpenAI 模型客户端 | `core/llm.py` | VectorStore 抽象（项目直连 Milvus） |
| Prompt 模板 | `core/prompts.py` | Document Loader 全家桶 |
| LCEL 管道 `\|` | `intent.py`、`coref.py` 等 | AgentExecutor（被 LangGraph 替代） |
| 结构化输出 | `core/llm.py` `structured()` | Memory 模块（项目自己实现三层上下文） |
| `@tool` 工具 | `tools/builtin/*.py` | OutputParser 系列（走 function calling） |
| 消息类型 | 全局 | Callback 体系（项目用 Langfuse 回调） |

> **AI解析**：为什么项目"绕开"了很多 LangChain 高级组件？因为**可控性**——
> AI 应用在生产环境里问题特别多（模型抽风、上游兼容坑、缓存失效），
> 高级组件封装越深越难排障。项目的哲学是：**用 LangChain 的底层原语，
> 自己掌控编排（这就是 LangGraph 出场的原因）**。
> 这也是给你的建议：先学会消息/管道/结构化输出/工具四个原语，就足够读懂 80% 的 AI 代码。

下一篇：**LangGraph**——本项目真正的骨架，所有节点、边、状态流转都在那里。
