# 站点发布说明（Qoder Sites）

本目录是「据典」的项目网站，纯静态（`index.html` + `assets/*.png`），无构建步骤、无外部 CDN。

## 发布步骤

1. 打开 Qoder（个人账号；企业版无法设置公开访问）；
2. 进入 **Sites → Add site**，选择「从已有项目/文件夹创建」；
3. 项目目录选本目录 `qoder/site`，入口 `index.html`；
4. 预览确认图片、锚点、代码块正常；
5. **Publish**，然后在 **Share → Change access scope** 选「任何人都可访问」；
6. 复制完整 URL，填入 `docs/提交材料.md` 的「项目网站网址」，并用无痕窗口验证；
7. 在 Sites 的「访问统计」里可看到访问次数与独立访客，可用于「使用效果」材料。

## 本地预览

```bash
cd qoder/site
python3 -m http.server 8899
# http://127.0.0.1:8899
```

> 发布后修改内容需要再次点击 Publish 才会更新线上版本；评审结束前不要删除站点。
