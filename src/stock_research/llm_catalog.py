"""Builtin catalog of domestic LLM providers and models (OpenAI-compatible).

Each model carries an optional ``thinking_levels`` map (统一强度档位 → 该模型
的实际请求参数，参考 pi 的 thinkingLevelMap 设计)。请求组装时把选中档位的
参数片段 merge 进 chat/completions body；档位缺失表示该模型不支持强度调节。

档位约定：``off`` / ``low`` / ``medium`` / ``high``。
"""

from __future__ import annotations

from typing import Any

# 统一强度档位，前端按此顺序展示。
THINKING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high")

LEVEL_LABELS: dict[str, str] = {"off": "关闭", "low": "低", "medium": "中", "high": "高"}

# 各家推理开关的参数形状（国内供应商并不统一，逐家映射）：
#   - 智谱 / 豆包(方舟): thinking: {type: enabled|disabled}
#   - 通义千问(百炼):    enable_thinking: true|false (+ 可选 thinking_budget)
#   - DeepSeek:         无档位参数（reasoner 固定深度思考，chat 固定关闭）
_THINKING_TOGGLE_ZHIPU = {"off": {"thinking": {"type": "disabled"}}, "low": {"thinking": {"type": "enabled"}}, "medium": {"thinking": {"type": "enabled"}}, "high": {"thinking": {"type": "enabled"}}}
_THINKING_TOGGLE_QWEN = {"off": {"enable_thinking": False}, "low": {"enable_thinking": True}, "medium": {"enable_thinking": True}, "high": {"enable_thinking": True, "thinking_budget": 32768}}
_THINKING_TOGGLE_ARK = _THINKING_TOGGLE_ZHIPU

BUILTIN_PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": (
            {"model_id": "deepseek-chat", "display_name": "DeepSeek-V3（对话）", "thinking_levels": {}},
            {"model_id": "deepseek-reasoner", "display_name": "DeepSeek-R1（深度思考，固定）", "thinking_levels": {}},
        ),
    },
    {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": (
            {"model_id": "kimi-latest", "display_name": "Kimi 最新版", "thinking_levels": {}},
            {"model_id": "kimi-k2-0905-preview", "display_name": "Kimi-K2", "thinking_levels": {}},
            {"model_id": "kimi-thinking-preview", "display_name": "Kimi 思考模型（固定）", "thinking_levels": {}},
        ),
    },
    {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": (
            {"model_id": "glm-4.6", "display_name": "GLM-4.6", "thinking_levels": _THINKING_TOGGLE_ZHIPU},
            {"model_id": "glm-4.5", "display_name": "GLM-4.5", "thinking_levels": _THINKING_TOGGLE_ZHIPU},
            {"model_id": "glm-4.5-air", "display_name": "GLM-4.5-Air", "thinking_levels": _THINKING_TOGGLE_ZHIPU},
        ),
    },
    {
        "name": "通义千问（百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": (
            {"model_id": "qwen-plus", "display_name": "Qwen-Plus", "thinking_levels": _THINKING_TOGGLE_QWEN},
            {"model_id": "qwen-max", "display_name": "Qwen-Max", "thinking_levels": _THINKING_TOGGLE_QWEN},
            {"model_id": "qwen3-235b-a22b-instruct-2507", "display_name": "Qwen3-235B", "thinking_levels": _THINKING_TOGGLE_QWEN},
        ),
    },
    {
        "name": "火山方舟（豆包）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": (
            {"model_id": "doubao-seed-1-6-250615", "display_name": "豆包 Seed 1.6", "thinking_levels": _THINKING_TOGGLE_ARK},
            {"model_id": "doubao-seed-1-6-flash-250815", "display_name": "豆包 Seed 1.6 Flash", "thinking_levels": _THINKING_TOGGLE_ARK},
            {"model_id": "doubao-1-5-pro-32k-250115", "display_name": "豆包 1.5 Pro", "thinking_levels": {}},
        ),
    },
    {
        "name": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": (
            {"model_id": "deepseek-ai/DeepSeek-V3", "display_name": "DeepSeek-V3（托管）", "thinking_levels": {}},
            {"model_id": "deepseek-ai/DeepSeek-R1", "display_name": "DeepSeek-R1（托管，固定）", "thinking_levels": {}},
            {"model_id": "Qwen/Qwen3-32B", "display_name": "Qwen3-32B", "thinking_levels": _THINKING_TOGGLE_QWEN},
        ),
    },
)


def normalize_level(level: Any) -> str:
    """Clamp an arbitrary level input to a canonical one (pi 的 clamp 思路)."""
    value = str(level or "").strip().casefold()
    if value in {"", "none", "default"}:
        value = "off"
    if value not in THINKING_LEVELS:
        return "off"
    return value


def parse_thinking_levels(raw: Any) -> dict[str, dict[str, Any]]:
    """Validate a stored/requested thinking_levels map; drop invalid entries."""
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for level, params in raw.items():
        key = str(level or "").strip().casefold()
        if key not in THINKING_LEVELS or not isinstance(params, dict) or not params:
            continue
        result[key] = params
    return {level: result[level] for level in THINKING_LEVELS if level in result}


def levels_for(thinking_levels: Any) -> list[str]:
    """Supported level keys for a model, sorted in canonical order."""
    parsed = parse_thinking_levels(thinking_levels)
    return [level for level in THINKING_LEVELS if level in parsed]


def default_level_for(thinking_levels: Any, default_level: Any = None) -> str:
    """Pick a model's default level (参考 deepseek-harness defaultEffort).

    显式声明的默认档位优先；未声明时支持档位的模型默认 ``high``
    （"The default balance for most tasks"），固定模型为 ``off``。
    """
    levels = levels_for(thinking_levels)
    if not levels:
        return "off"
    raw = str(default_level or "").strip().casefold()
    if raw in levels:
        return raw
    return "high" if "high" in levels else levels[0]


def params_for_level(thinking_levels: Any, level: Any) -> dict[str, Any]:
    """Resolve the request-parameter fragment for a level (空档位返回 {})."""
    parsed = parse_thinking_levels(thinking_levels)
    return parsed.get(normalize_level(level), {})
