# sync-wetest-dns

从 wetest.vip 获取 Cloudflare 优选 IP，按**运营商 + 地区**分配到不同子域名。

### 策略

wetest.vip 的 API 返回 4 组数据，每组按 colo 偏好 + 延迟排序后写入对应的子域名：

| API 分组 | 运营商 | 建议子域名 | 典型 colo |
|---------|--------|-----------|----------|
| `CM` | 移动 | `cm.example.com` | HKG（香港） |
| `CU` | 联通 | `cu.example.com` | SJC/LAX（美国西海岸） |
| `CT` | 电信 | `ct.example.com` | SIN（新加坡） |
| `CN` | 三网官方优选 | `cf.example.com` | 混合 |

这样客户端可以按运营商分流，同一运营商的用户始终访问该运营商下延迟最低的优选 IP。

### 设置

**1. 在 Settings → Secrets → Actions 添加以下 Secret**

| Secret | 说明 | 示例 |
|--------|------|------|
| `CF_API_TOKEN` | Cloudflare API Token（DNS:Edit 权限） | |
| `CF_ZONE_ID` | 域名 Zone ID | |
| `DOMAIN_MAP` | JSON，运营商→子域名映射 | `{"CM":"cm.example.com","CU":"cu.example.com","CT":"ct.example.com","CN":"cf.example.com"}` |

**可选 Secrets：**

| Secret | 说明 | 默认 |
|--------|------|------|
| `MAX_RECORDS` | 每组保留几条 A 记录 | `2` |
| `PREFER_COLO` | 首选数据中心，逗号分隔 | 不限制 |

### JSON 结构说明

`DOMAIN_MAP` 是一个 JSON 字符串，key 固定为 `CM`、`CU`、`CT`、`CN`：

```json
{
  "CM": "移动的域名",
  "CU": "联通的域名",
  "CT": "电信的域名",
  "CN": "三网优选的域名"
}
```

不需要全部填满，只填你需要的。比如你只要两个：

```json
{
  "CM": "cm.example.com",
  "CN": "cf.example.com"
}
```

### PREFER_COLO 示例

优先香港和新加坡的 IP 给移动：

```
PREFER_COLO=HKG,SIN
```

有 HKG 的 IP 会排最前面，只有 HKG 不够才补其他地区的。

### 客户端用法

Clash 配置：

```yaml
proxy-groups:
  - name: "移动"
    type: url-test
    proxies:
      - "cm.example.com"
    url: "http://www.gstatic.com/generate_204"
    interval: 60

  - name: "联通"
    type: url-test
    proxies:
      - "cu.example.com"
    url: "http://www.gstatic.com/generate_204"
    interval: 60

  - name: "优选"
    type: url-test
    proxies:
      - "cf.example.com"
    url: "http://www.gstatic.com/generate_204"
    interval: 60
```
