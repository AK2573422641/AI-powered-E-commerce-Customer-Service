# 第 1 篇：Python 语法速成 — 6 年 Java 经验者的对照手册

> 本篇只讲「读懂本项目源码所需」的 Python 语法，全部配 Java 类比。
> 注解以 **AI解析** 开头。读完这篇，第 5 篇的源码带读就不会被语法卡住。

## 1. 模块与导入：import ≈ Java 的 package + import

Java 中一个文件一个 public 类，包名 `com.foo.bar`，用 `import com.foo.bar.Baz;`。
Python 中一个 `.py` 文件就是一个「模块（module）」，一个目录（带 `__init__.py`）是一个「包（package）」。

```python
# app/graph/nodes.py 的真实开头
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from app.config import settings
from app.core import coref
```

> **AI解析**：`from 包.模块 import 名字` 等价于 Java 的 `import 包.模块.名字`。
> 注意 Python 没有「类名必须和文件名一致」的规矩——一个文件里可以放很多函数、类，**文件名就是模块名**，
> 所以 `app/core/llm.py` 里没有 `class Llm`，而是一堆函数。`from app.core import coref` 导进来的
> `coref` 是**模块对象**（可以理解为一个静态工具类），之后用 `coref.resolve(...)` 调它里面的函数。
>
> Java 类比：`app/core/llm.py` ≈ `app/core/LlmUtils.java`（全静态方法的工具类），
> 而不是 `Llm.java`（实体类）。

```python
# app/core/llm.py 里函数内部才 import（延迟导入）
def structured(schema, ...):
    from langchain_core.runnables import RunnableLambda
```

> **AI解析**：Python 允许在函数内部 import，好处有二：① 打破循环依赖（类似 Java 里两个类互相引用时
> 用 setter 注入）；② 推迟加载耗时模块（用到才加载）。Java 里 import 只是编译期语法，
> 运行时由类加载器按需加载；Python 的 import 则是**执行语句**，写在哪就在哪执行。

## 2. 类型注解：`str | None` ≈ `@Nullable String`

```python
# app/graph/nodes.py
def _extract_order_id(text: str) -> str | None:
    m = _ORDER_RE.search(text or "")
    return m.group(1) if m else None
```

> **AI解析**：`str | None` 读作「字符串或空」，等价 Java 的 `@Nullable String` / `Optional<String>`。
> `-> str | None` 是返回值注解，等价 Java 方法签名 `String _extract_order_id(String text)`。
> 关键区别：**Python 的类型注解运行时不做任何检查**，它只是「文档 + 给 IDE/类型检查器看的」。
> 类比 Java：就像注解不影响运行一样，Python 的类型提示也只是元数据。项目里靠它 + Pydantic
> 实现「自愿的类型安全」。
>
> 另一个高频写法：`list[dict]`、`dict[str, ToolSpec]`、`tuple[int, int]`，
> 等价 Java 的 `List<Map<...>>`、`Map<String, ToolSpec>`。Python 3.9 起小写即可，不用 `List`。

```python
# app/graph/state.py 中的联合与可选
def merge_dict(a: dict | None, b: dict | None) -> dict:
```

> **AI解析**：`a: dict | None` = 参数可为 dict 或 None。`None` 就是 Java 的 `null`，
> 但 Python 把 `None` 当成一个真实对象（`NoneType` 的唯一实例），可以安全地 `is None` 判断。

## 3. 函数：def、默认参数、关键字参数

```python
# app/core/retrieval.py
async def search_knowledge(
    query: str, strategy: str = "hybrid_rerank", top_k: int | None = None,
    category: str | None = None, client=None, collection: str = "knowledge",
    bm25_text: str | None = None, split: bool = True,
) -> list[dict]:
```

> **AI解析**：`def 函数名(参数)` 等价 Java 方法。四个重点：
> 1. **默认参数**：`strategy: str = "hybrid_rerank"`——Java 没有这个，Java 要用方法重载（Overload）
>    或 Builder 模式模拟。Python 直接给参数设默认值。
> 2. **调用时可用关键字参数**：`search_knowledge("运费", strategy="bm25", top_k=5)`。
>    等价 Java 的 Builder：`search().query("运费").strategy("bm25").build()`。
>    Java 靠位置传参（容易传错顺序），Python 靠名字传参（`top_k=5` 一眼看懂）。
> 3. **类型注解可选**：`client=None` 没写注解，Python 允许省略。
> 4. **缩进即语法**：Python 用 4 个空格缩进表示「代码块」，**没有花括号**。函数体靠缩进划界，
>    缩进错了逻辑就错了（这就是为什么 Python 代码看起来这么整齐）。

> **AI解析（参数默认值的经典坑）**：Java 程序员最容易踩的是「可变默认值」——
> Python 的默认值在函数定义时**只求值一次**，所以 `def f(x, lst=[])` 大家共享同一个列表。
> 本项目全部规避了这个坑（默认值都是 None/常量）。

## 4. 类：class、`__init__`、self、dataclass

```python
# app/tools/registry.py（简化）
@dataclass
class ToolSpec:
    name: str
    description: str
    json_schema: dict
    tool: BaseTool
    permission: str
    source: str
    mcp_server: str | None = None
    timeout: float | None = None
```

> **AI解析**：`@dataclass` 是 Python 自带的「代码生成器」装饰器：
> 你只写字段，它自动生成构造函数、`equals`、`toString`（`__init__`/`__eq__`/`__repr__`）。
> 等价 Java 的 **record** 或 Lombok 的 `@Data + @AllArgsConstructor`。
> - `str | None = None` 的字段有默认值，等价 Java 构造器重载出的「可选参数」。
> - `ToolSpec(name=..., description=...)` 实例化时可以用关键字参数，只传部分字段。
> - Java 的 `record` 是不可变的；`dataclass` 默认可变（可用 `frozen=True` 变不可变，
>   项目里 `budget.py` 的 `ContextBudget` 就用了 `@dataclass(frozen=True)`）。

普通类长这样（项目里的 `SessionStore`）：

```python
class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, list[BaseMessage]] = {}

    def get(self, session_id: str) -> list[BaseMessage]:
        return self._sessions.get(session_id, [])
```

> **AI解析**：`__init__` 就是 Java 的构造函数，`self` 就是 Java 的 `this`——
> 但 Python **必须显式把 self 写成第一个参数**（Java 是隐式的）。
> `def get(self, session_id)` ≈ Java 的 `List<BaseMessage> get(String sessionId)`。
> `_sessions` 以下划线开头是「约定私有」——等价 Java 的 `private`，但只是**约定**，
> 外部硬要访问也能访问（Python 哲学：大家都是成年人）。

## 5. Pydantic：运行时的「Java Bean + Bean Validation」

Java 里你可能写过：实体类 + `@NotNull` + `@Size(max=3)` 注解 + Validator 校验。
Python 里对应的是 **Pydantic**（本项目所有配置和结构化输出都用它）：

```python
# app/core/intent.py
class _Intent(BaseModel):
    intent: Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "人工", "闲聊", "其他"] = Field(
        description="九类意图之一")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="判断把握 0-1")
```

> **AI解析**：`BaseModel` 是 Pydantic 的基类，等价 Java 的「带校验的 DTO」：
> - `Literal[...]` 等价 Java 枚举约束（只能取列出的值之一）；
> - `float = Field(default=0.5, ge=0.0, le=1.0)` 等价 `@DecimalMin("0.0") @DecimalMax("1.0")`；
> - **构造时自动校验**，不合法直接抛 `ValidationError`（类似 Bean Validation 的 ConstraintViolationException）。
> - `description=` 是给大模型看的说明！这个类被用来约束大模型的输出格式（第 3 篇详解）。
>
> 另一大用途是「配置类」：`app/config.py` 的 `Settings(BaseSettings)` 会把 `.env` 文件
> 和系统环境变量自动映射成字段，等价 Spring Boot 的 `@ConfigurationProperties`——
> `.env` 里写 `CHAT_MODEL=xxx` 就自动填进 `settings.chat_model`。

## 6. TypedDict：给 dict 套一个「接口」

```python
# app/graph/state.py
class ConversationState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    intent: str
    ...
```

> **AI解析**：`TypedDict` 是「给普通字典 dict 加类型标注」——运行时它仍然是个 dict
> （所以 LangGraph 能对它做合并、序列化），但 IDE 能从中知道「有哪些键、每个键什么类型」。
> 等价 Java：一个只有 getter/setter 的 DTO 接口定义，但**底层是 `Map<String, Object>`**。
> - `total=False` 表示所有键都可选（不是每个 key 都必须存在）。
> - `state.get("intent", "")` 等价 Java `map.getOrDefault("intent", "")`。
> - 为什么用 dict 不用类？因为 LangGraph 要对 State 做**增量合并**（每个节点只返回自己改动的键），
>   dict 天生支持「合并两个 map」，类不行。这在第 4 篇会展开。

## 7. f-string 与字符串格式化 ≈ String.format

```python
# app/graph/runtime.py
logger.info("层1 降级 conv=%s %s→%s(层1 %s > 预算 %s)", cid, layer1_from, new_from, layer1_tok, l1_budget)
# app/core/summarizer.py（f-string 写法）
dialog = "\n".join(f"{'用户' if m.role == 'user' else '客服'}:{m.content}" for m in seg if m.content)
```

> **AI解析**：`f"..."` 叫 f-string，花括号里直接写表达式，等价 Java 的 `String.format("...%s", x)`
> 或 Kotlin 的字符串模板。两个高频技巧：
> - `f"{r.returncode}"`：普通插值。
> - `f"{score:.3f}"`：格式化，`.3f` = 保留 3 位小数（等价 `String.format("%.3f", score)`）。
> - 日志用 `%s` 占位 + 参数分离写法（`logger.info("...%s", x)`）：**日志库只在需要输出时才拼字符串**，
>   等价 SLF4J 的 `log.info("{}", x)`——不要用 f-string 拼好再传，那样即使日志级别不输出也白拼了。

## 8. 列表推导式 ≈ Stream 的 map/filter

```python
# app/core/retrieval.py
clauses = [p.strip() for p in _CLAUSE.split(query) if len(p.strip()) >= _MIN_CLAUSE]
```

> **AI解析**：读法 =「对 _CLAUSE.split(query) 里每个 p，满足条件的取 p.strip()，
> 组成一个新列表」。等价 Java：
> ```java
> query.split(...).stream().map(String::strip).filter(p -> p.length() >= 4).collect(toList());
> ```
> 还有字典推导式：`merged = {s.name: s for s in specs}` ≈ `Collectors.toMap(ToolSpec::name, s -> s)`。

## 9. 装饰器 ≈ 注解 + AOP

```python
# app/tools/builtin/orders.py
@tool
async def query_order(
    order_id: Annotated[str, Field(description="订单号,例如 1001")],
    user_id: Annotated[str, InjectedToolArg],
) -> dict:
    """查询订单的状态、金额、下单时间、商品名和物流单号(tracking_no)。..."""
```

> **AI解析**：`@tool` 把普通函数**包装成一个「工具对象」**：函数名变成工具名，
> **docstring（函数下方三引号的文档字符串）变成工具描述**，参数注解变成参数 JSON Schema。
> 大模型就是靠这些描述决定「什么时候调哪个工具」。
> Java 类比：`@Service @RequestMapping` + 反射扫描——但 Python 装饰器是**运行时直接改函数对象**，
> 不需要容器扫描，`@tool` 一执行函数就变成了工具。**docstring 在这里是「给 AI 看的接口文档」，务必写好。**
>
> `Annotated[str, Field(description="订单号,例如 1001")]` 拆开看：
> - 类型是 `str`；
> - 附带元数据「描述是 订单号」——写入给模型的 JSON Schema；
> - `InjectedToolArg` 表示「这个参数不由模型填，系统注入」（防伪造身份，见第 5 篇 engine.py 解读）。

## 10. `with` 语句 ≈ try-with-resources

```python
# app/kb/documents.py（示意）
with open(path, encoding="utf-8") as f:
    content = f.read()
```

> **AI解析**：`with` 叫「上下文管理器」，退出代码块时自动调用清理逻辑（关文件/关连接），
> 等价 Java 的 `try (var f = ...) { ... }`。项目里更复杂的用法是 `runtime.py` 里的
> `AsyncSqliteSaver.from_conn_string(...)` 返回的异步上下文管理器——后面第 5 篇详解。

## 11. True/False 的「真假值」判断

```python
# app/graph/routing.py
last = state["messages"][-1]
has_tool_calls = isinstance(last, AIMessage) and bool(last.tool_calls)
```

> **AI解析**：`isinstance(x, AIMessage)` ≈ Java 的 `x instanceof AIMessage`。
> Python 判断「真假」很宽松：空列表 `[]`、空字典 `{}`、空字符串 `""`、`None`、`0` 都算假。
> 所以 `if not intr:` 的意思是「intr 是 None 或空」。`bool(x)` 强制转成布尔。
> Java 程序员注意：Java 里 `if (list != null && !list.isEmpty())` 这种防御，
> Python 里直接 `if list:` 就够了。

## 12. `__name__ == "__main__"` ≈ public static void main

```python
# scripts/ 下的脚本常见
if __name__ == "__main__":
    asyncio.run(main())
```

> **AI解析**：每个 Python 文件既是「可导入的模块」也是「可执行的脚本」。
> `__name__` 是内置变量：文件被直接运行时它等于 `"__main__"`，被 import 时等于模块名。
> 所以这行的意思是「只有直接运行本文件时才执行」——等价 Java 的 main 方法
> （Java 里 main 是入口，Python 里任何文件都可以当入口）。

## 13. 命名约定速查（对照 Java）

| Python 写法 | 含义 | Java 对应 |
| - | - | - |
| `name` | 公开 | `public` |
| `_name` | 内部使用（约定私有） | `private`（约定，非强制） |
| `__name` | 类内强私有（名称改写） | `private` |
| `__init__` | 构造函数 | 构造器 |
| `__str__` / `__repr__` | 转字符串 | `toString()` |
| `CONSTANT` | 模块级常量 | `static final` |
| `snake_case` | 变量/函数命名（项目统一风格） | Java 用 camelCase |
| `PascalCase` | 类名 | 同 Java |

> **AI解析**：项目里的函数名全是 `snake_case`（如 `search_knowledge`、`build_window`），
> 别觉得别扭，这是 Python 社区铁律（PEP 8）。看到 `_user_text(state)` 就知道是模块内部辅助函数。

## 14. 一文速查：常见语法对照总表

| Python | Java 等价物 |
| - | - |
| `def f(a, b=1):` | `void f(A a, B b)` + 重载/Builder |
| `str \| None` | `@Nullable String` / `Optional<String>` |
| `dataclass` | `record` / Lombok `@Data` |
| `BaseModel`（Pydantic） | Bean + `@Valid` 注解 |
| `TypedDict` | DTO 接口 + `Map<String,Object>` 底层 |
| `@tool`（装饰器） | `@Service` + 反射注册 |
| `f"{x:.2f}"` | `String.format("%.2f", x)` |
| `[x for x in xs if p(x)]` | `stream().filter().map()` |
| `with open(...) as f:` | `try (var f = ...) {}` |
| `state.get("k", "")` | `map.getOrDefault("k", "")` |
| `if not items:` | `if (items == null \|\| items.isEmpty())` |
| `async def` / `await` | `CompletableFuture` / 虚拟线程（第 2 篇） |
| `None` | `null`（但是个真对象，用 `is None` 判断） |
| `# 注释` | `// 注释` |
| `"""文档字符串"""` | Javadoc（但对 AI 是「接口描述」，见 `@tool`） |
| 缩进分块 | `{}` 花括号分块 |

## 15. 本篇小结与下一步

- Python 是「动态类型 + 自愿注解」，类型安全靠 Pydantic 补齐（≈ Java Bean Validation）；
- 函数是一等公民，模块 ≈ 工具类文件，`snake_case` 是命名规范；
- 装饰器 = 注解 + AOP 的合体，`@tool` 是 AI 应用开发的核心入口；
- docstring 在 AI 时代有了新角色：**写给大模型看的接口文档**。

下一篇进入重头戏：**异步与多线程**——你会看到本项目如何用
`asyncio.create_task` 在后台跑摘要任务、如何用单线程执行器驾驭 Milvus 客户端。
