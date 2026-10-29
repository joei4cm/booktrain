# 示例书

`sample.txt` —— 一段公共领域风格的短篇（原创文字，非任何受版权保护作品），
用来跑通全流程：

```bash
booktran init examples/sample-book/sample.txt --dir /tmp/demo --fake
booktran run   -p /tmp/demo --fake --yes
booktran proofread -p /tmp/demo --fake
booktran preview  -p /tmp/demo
booktran export   -p /tmp/demo --bilingual
```

`--fake` 用假 provider，**零成本、不需要 API key**，是 CI 冒烟测试用的同一条路径。
