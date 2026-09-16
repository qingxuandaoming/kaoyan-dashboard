# mermaid（vendored）

大盘读笔记时把 ```mermaid 代码块渲染成图。与 `tools/d3.min.js`、`tools/katex/`
一样放在本地，不走 CDN——大盘要在断网时也能读笔记。

| | |
|---|---|
| 来源 | `https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js` |
| 版本 | 11.17.2 |
| 构建 | UMD（挂 `window.mermaid`） |
| 大小 | 3,572,661 字节 |
| sha256 | `581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8` |
| 许可 | MIT |

## 升级

```bash
curl -sL -o src/tools/mermaid/mermaid.min.js \
  "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
sha256sum src/tools/mermaid/mermaid.min.js   # 同步更新上表
```

升级后请打开一篇含图的笔记（如 `408/DS/第7章_查找.md`）确认仍能渲染。
