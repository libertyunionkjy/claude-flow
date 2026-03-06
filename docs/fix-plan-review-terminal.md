# Bug Fix: `cf plan review` 键盘输入失效

> 日期: 2026-03-06
> 影响版本: v0.1.0
> 修复状态: 已修复

## 问题描述

运行 `cf plan review` 进入交互式审批时，用户在 `click.prompt` 处：

- **回车键无效** — 字符可回显，但按 Enter 不提交输入
- **Ctrl+C 无效** — 无法中断进程

导致 review 流程完全无法使用。

## 根因分析

### 直接原因

`planner.py` 中调用 `claude` CLI 的 `subprocess.Popen` **未隔离 stdin**：

```python
# planner.py:45 (修复前)
proc = subprocess.Popen(
    cmd, cwd=str(self._root),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    # 缺少 stdin=subprocess.DEVNULL
)
```

`claude` CLI 子进程继承了父进程的 stdin 文件描述符，获得了对终端的完全控制权。

### 触发机制

```
用户终端 (canonical mode)
    │
    ├── cf plan          # 调用 claude CLI 生成计划
    │   └── claude CLI   # 继承 stdin，修改终端为 raw mode
    │       └── 退出     # 终端未恢复 canonical mode
    │
    └── cf plan review   # 此时终端仍处于 raw mode
        └── click.prompt()
            └── input()  # 等待 \n (LF)，但 raw mode 下 Enter 发送 \r (CR)
                         # → 永远收不到 \n，表现为"回车无效"
```

### 终端模式对比

| 特性 | Canonical Mode (正常) | Raw Mode (被破坏后) |
|------|----------------------|---------------------|
| Enter 键 | 发送 `\r` → 内核自动转换为 `\n` (ICRNL) | 发送 `\r`，无转换 |
| Ctrl+C | 内核产生 SIGINT (ISIG) | 发送字节 `0x03`，无信号 |
| 输入回显 | 内核自动回显 (ECHO) | 可能仍有回显 |
| 行编辑 | 支持退格等 (ICANON) | 逐字节传递，无缓冲 |

Python `input()` 底层调用 `readline`，期望接收 `\n` 作为行结束符。当 ICRNL 标志被清除后，Enter 键的 `\r` 不再被转换为 `\n`，导致 `input()` 持续阻塞等待。

## 修复方案

采用**两层防御**策略：

### 第一层：源头隔离（根本修复）

所有 `subprocess.Popen` / `subprocess.run` 调用添加 `stdin=subprocess.DEVNULL`，阻断子进程对父终端 stdin 的访问：

```python
# planner.py — Popen 调用
proc = subprocess.Popen(
    cmd, cwd=str(self._root),
    stdin=subprocess.DEVNULL,       # 新增
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)

# worker.py — subprocess.run 调用（3 处）
result = subprocess.run(
    cmd, cwd=str(wt_path),
    stdin=subprocess.DEVNULL,       # 新增
    capture_output=True, text=True,
    timeout=self._cfg.task_timeout,
)
```

**原理**：子进程的 stdin 指向 `/dev/null` 而非终端，无法调用 `tcsetattr()` 修改终端属性。

### 第二层：运行时恢复（防御性修复）

在 `cli.py` 中新增终端重置函数，每次 `click.prompt` 前调用：

```python
def _reset_terminal() -> None:
    """Reset terminal to canonical mode for interactive input."""
    if not sys.stdin.isatty():
        return
    try:
        import termios
        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
        attrs[3] |= termios.ICANON | termios.ECHO | termios.ISIG  # 恢复 local flags
        attrs[0] |= termios.ICRNL                                  # 恢复 CR→NL 转换
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except (ImportError, termios.error, ValueError, OSError):
        pass
```

同时新增 `_strip_ansi()` 清理 plan 内容中可能存在的 ANSI 转义序列，防止文件内容污染终端状态。

## 修改文件清单

| 文件 | 位置 | 变更内容 |
|------|------|----------|
| `claude_flow/planner.py` | L46 | `Popen` 添加 `stdin=subprocess.DEVNULL` |
| `claude_flow/worker.py` | L70, L196, L214 | 3 处 `subprocess.run` 添加 `stdin=subprocess.DEVNULL` |
| `claude_flow/cli.py` | L25-48 | 新增 `_strip_ansi()` 和 `_reset_terminal()` |
| `claude_flow/cli.py` | plan_review | 3 处 `click.prompt` 前调用 `_reset_terminal()`，plan 显示前调用 `_strip_ansi()` |

## 验证

- 40 个单元测试全部通过，无回归
- `_strip_ansi()` 正确处理 CSI 序列、OSC 序列和单字符转义
- `_reset_terminal()` 在非 TTY 环境下静默跳过

## 经验总结

1. **子进程必须隔离 stdin** — 任何不需要交互输入的子进程都应设置 `stdin=subprocess.DEVNULL`，防止其修改父进程的终端状态
2. **终端状态不可假设** — 在交互式 prompt 前应主动恢复终端到已知状态，而非假设终端处于正常模式
3. **防御性显示** — 显示外部生成的文本内容前应清理潜在的终端控制序列
