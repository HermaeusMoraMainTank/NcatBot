# 表情图库来源

本目录下每个子目录 = 一套图库，`pack.json` 存每张图的情绪（mood）、画面描述（caption）和检索关键词。

| 图库 | 张数 | 许可 | 来源 |
| --- | ---: | --- | --- |
| `darkdog` | 54 | 自有 | 根目录散图（原有图库） |
| `dafeiyu-001` | 24 | personal | [dsh-meme-packs](https://github.com/yyh-001/dsh-meme-packs) 大肥鱼 |
| `dafeiyu-learned` | 31 | personal | 同上，大肥鱼·学图 |
| `whale-girl-emoji` | 91 | noncommercial-fan | 同上，鲸鱼娘表情包 |
| `paopao-whale-girl` | 55 | CC-BY-NC-SA-4.0 | 同上，泡泡表情包（已压缩到 640px） |

## 情绪桶

模型只认 6 个桶，细 tag 在 `pack.json` 里保留：

- `happy`：happy / like / meow / givemoney / color
- `angry`：angry / fool / baka
- `sad`：sad / sigh
- `shy`：shy
- `confused`：confused / surprised / see
- `daily`：daily / sleep / morning / work / cpu / reply

## 使用方式

- `sticker_search(mood=..., query=...)` 返回候选（名字｜情绪｜画面）
- `send_sticker(名字)` 发送；名字支持按描述模糊匹配
- `sticker_vibe(topic, mood)` 一步挑一张

## 更换 / 新增图库

把图片放进 `data/fakeai/stickers/<库名>/`，再写一个 `pack.json`：

```json
{
  "id": "my-pack",
  "name": "我的图库",
  "version": "1.0.0",
  "license": "personal",
  "images": {
    "文件名（不含扩展名）": {
      "mood": "happy",
      "caption": "画面里有什么，一句话",
      "keywords": "空格分隔的检索词"
    }
  }
}
```

没有 `pack.json` 也能用，只是模型只能靠文件名猜，选图会不准。
