# 任务卡 06 · PowerContext 重启记忆验收

- 时间：2026-10-28
- 操作：`scripts/pc_server.sh restart`（pid 46708 → 57080）
- 结果：重启前后 `GET /api/memory` 逐字节一致，profile.topic 与 33 条 entries 均保留。

## 重启前
```json
{
    "connected": true,
    "source": "powercontext",
    "scope_id": "scp_341j74tk1jtwj4k0vjnmdwf03t",
    "entries": 33,
    "profile": {
        "topic": "seekdb \u5185\u6838\u5f00\u53d1\uff1a\u4ece\u7f16\u8bd1\u5230\u8c03\u8bd5",
        "style": "concise",
        "goal": "\u8bfb\u5b8c\u5185\u5b58\u4e0e\u65e5\u5fd7\u6587\u6863\uff0c\u8dd1\u901a\u4e00\u6b21\u672c\u5730\u7f16\u8bd1",
        "saved_at": "2026-10-28T14:07:26+00:00"
    },
    "progress": {
        "asked": 13,
        "last_question": "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f",
        "last_confidence": 0.6956,
        "last_mode": "insufficient",
        "last_docs": [],
        "weak_topics": [
            "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f"
        ],
        "updated_at": "2026-10-28T13:26:54+00:00"
    },
    "recent": [
        {
            "topic": "seekdb \u5185\u6838\u5f00\u53d1\uff1a\u4ece\u7f16\u8bd1\u5230\u8c03\u8bd5",
            "style": "concise",
            "goal": "\u8bfb\u5b8c\u5185\u5b58\u4e0e\u65e5\u5fd7\u6587\u6863\uff0c\u8dd1\u901a\u4e00\u6b21\u672c\u5730\u7f16\u8bd1",
            "saved_at": "2026-10-28T14:07:26+00:00"
        },
        {
            "topic": "\u91cd\u542f\u540e\u4ecd\u5728\u00b7\u8bb0\u5fc6\u9a8c\u6536",
            "style": "concise",
            "goal": "\u9a8c\u8bc1 PowerContext \u91cd\u542f\u6301\u4e45\u5316",
            "saved_at": "2026-10-28T14:07:08+00:00"
        },
        {
            "topic": "seekdb \u5185\u6838\u5f00\u53d1\uff1a\u4ece\u7f16\u8bd1\u5230\u8c03\u8bd5",
            "style": "concise",
            "goal": "\u8bfb\u5b8c\u5185\u5b58\u4e0e\u65e5\u5fd7\u6587\u6863\uff0c\u8dd1\u901a\u4e00\u6b21\u672c\u5730\u7f16\u8bd1",
            "saved_at": "2026-10-28T13:26:55+00:00"
        },
        {
            "topic": "\u636e\u5178\u9a8c\u8bc1 \u00b7 10-28 21:26",
            "style": "concise",
            "goal": "\u9a8c\u8bc1 PowerContext \u5199\u5165\u4e0e\u8bfb\u56de",
            "saved_at": "2026-10-28T13:26:55+00:00"
        },
        {
            "asked": 13,
            "last_question": "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f",
            "last_confidence": 0.6956,
            "last_mode": "insufficient",
            "last_docs": [],
            "weak_topics": [
                "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f"
            ],
            "updated_at": "2026-10-28T13:26:54+00:00"
        }
    ]
}
```

## 重启后
```json
{
    "connected": true,
    "source": "powercontext",
    "scope_id": "scp_341j74tk1jtwj4k0vjnmdwf03t",
    "entries": 33,
    "profile": {
        "topic": "seekdb \u5185\u6838\u5f00\u53d1\uff1a\u4ece\u7f16\u8bd1\u5230\u8c03\u8bd5",
        "style": "concise",
        "goal": "\u8bfb\u5b8c\u5185\u5b58\u4e0e\u65e5\u5fd7\u6587\u6863\uff0c\u8dd1\u901a\u4e00\u6b21\u672c\u5730\u7f16\u8bd1",
        "saved_at": "2026-10-28T14:07:26+00:00"
    },
    "progress": {
        "asked": 13,
        "last_question": "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f",
        "last_confidence": 0.6956,
        "last_mode": "insufficient",
        "last_docs": [],
        "weak_topics": [
            "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f"
        ],
        "updated_at": "2026-10-28T13:26:54+00:00"
    },
    "recent": [
        {
            "topic": "seekdb \u5185\u6838\u5f00\u53d1\uff1a\u4ece\u7f16\u8bd1\u5230\u8c03\u8bd5",
            "style": "concise",
            "goal": "\u8bfb\u5b8c\u5185\u5b58\u4e0e\u65e5\u5fd7\u6587\u6863\uff0c\u8dd1\u901a\u4e00\u6b21\u672c\u5730\u7f16\u8bd1",
            "saved_at": "2026-10-28T14:07:26+00:00"
        },
        {
            "topic": "\u91cd\u542f\u540e\u4ecd\u5728\u00b7\u8bb0\u5fc6\u9a8c\u6536",
            "style": "concise",
            "goal": "\u9a8c\u8bc1 PowerContext \u91cd\u542f\u6301\u4e45\u5316",
            "saved_at": "2026-10-28T14:07:08+00:00"
        },
        {
            "topic": "seekdb \u5185\u6838\u5f00\u53d1\uff1a\u4ece\u7f16\u8bd1\u5230\u8c03\u8bd5",
            "style": "concise",
            "goal": "\u8bfb\u5b8c\u5185\u5b58\u4e0e\u65e5\u5fd7\u6587\u6863\uff0c\u8dd1\u901a\u4e00\u6b21\u672c\u5730\u7f16\u8bd1",
            "saved_at": "2026-10-28T13:26:55+00:00"
        },
        {
            "topic": "\u636e\u5178\u9a8c\u8bc1 \u00b7 10-28 21:26",
            "style": "concise",
            "goal": "\u9a8c\u8bc1 PowerContext \u5199\u5165\u4e0e\u8bfb\u56de",
            "saved_at": "2026-10-28T13:26:55+00:00"
        },
        {
            "asked": 13,
            "last_question": "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f",
            "last_confidence": 0.6956,
            "last_mode": "insufficient",
            "last_docs": [],
            "weak_topics": [
                "seekdb \u652f\u6301 MongoDB \u534f\u8bae\u5417\uff1f"
            ],
            "updated_at": "2026-10-28T13:26:54+00:00"
        }
    ]
}
```
