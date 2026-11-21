# GeoBlue 家庭报销 Agent

[English](README.md) | 简体中文

这是一个独立的 Codex skill 和本地命令行工具，用于整理 GeoBlue / BCBS Global Solutions 家庭医疗账单。它从收件目录读取材料，记录字段来源，核对原币金额和重复账单，并通过会员网站准备理赔草稿。用户确认具体申请后才正式提交，也可以查询进度和处理补件。

## 开始使用

克隆本仓库后，在项目根目录使用 Python 3.9 或更新版本运行：

```sh
python3 -m geoblue_claims doctor
python3 -m geoblue_claims init
python3 -m geoblue_claims install-skill
```

在 Codex 打开项目，将 PDF、JPG、PNG 或 HEIC 材料放进本项目的 `inbox/`，然后输入：

> 使用 $geoblue-claims 按快速流程处理新账单，完成官网草稿后停在最终提交前，逐笔给我确认。

首次使用时，在官方会员网站登录，然后按[配置指南](skills/geoblue-claims/references/browser-and-setup.md)核对保单、患者访问权限和已有收款方式。认证凭据由浏览器和会员网站处理。

常用请求：

- “查看待确认申请。”
- “补充这笔材料。”
- “查询报销进度。”
- “这笔款已到账。”

快速流程会复用本地已核验的成员档案和收款方式。获得该笔申请的填写和上传授权后，再按页面批量填写官网草稿。完成后停在授权页，展示患者、费用、附件和收款方式摘要。你确认这一笔后，才签名、勾选声明并提交。内容或附件变化时需重新确认。

## 运行依赖

Python 辅助工具使用标准库。材料识别由当前 Codex 模型完成，官网操作需要 Codex 的浏览器工具。`install-skill` 将源码链接到项目的 `.agents/skills`。如果当前会话还没识别 `$geoblue-claims`，可重新打开项目或让 Codex 读取 [skill 文件](skills/geoblue-claims/SKILL.md)。

PDF 需要 Poppler 的 `pdfinfo`、`pdftotext`、`pdftoppm`；Mac 上可通过 `brew install poppler` 安装。HEIC 使用 macOS 自带的 `sips`。单文件上限 100 MiB，PDF 上限 100 页，超出时会保留原件并提示拆分。

本机 HEIC 原生编解码已在沙箱外通过测试；受限沙箱内可能需要为本地转换命令放行系统编解码访问。原件始终保留。

## 本地数据和行为

- `inbox/`：你放入的材料。扫描只复制，不删除或移动原件。
- `data/documents/`：按内容哈希保存的原件、PDF 文本及逐页预览。
- `data/inputs/`：成员、保单、申请和状态观察的输入 JSON。
- `data/portal-preferences.json`：可选的本地填表偏好。收款方式的掩码标识来自已核验的成员档案。
- `data/artifacts/`：真实网站回执、状态截图和理赔说明。
- `data/claims/`：你确认的申请快照；SQLite 台账位于 `data/claims.sqlite3`。

Git 忽略 `inbox/` 和 `data/`。文件权限限制了本机访问，存储层本身未加密。需要备份时，将整个 `data/` 放入私密备份。

所有 Python 命令只操作本地文件和记录，不会连接保险公司、发送邮件或自动操作浏览器。网页操作由 skill 协调 Codex 的浏览器工具执行。CLI 的确认参数是流程记录，不能替代用户在聊天中的真实确认。

日常从 `intake` 开始。它扫描收件目录，准备新材料并汇总待办。已归档到申请的材料无需重复渲染。已保存的家庭成员、电话、诊所和收款偏好优先复用。[快速流程](skills/geoblue-claims/references/fast-path.md)

真实账单用 `prepare` 模式准备正常名称的草稿；测试用 `test` 和 `TEST-DO-NOT-SUBMIT` 名称。两种模式均不能签名、勾选声明或点击最终 Submit。取得官网草稿最终审核的实际确认后，才新建 `production` 执行并检查提交 guard。模块未指定模式时仍默认为测试模式。

提交超时或中断会留下“结果未知”的记录；需先核实官网是否已收到，再决定后续操作。网站 `Complete` 只记录为“已处理”，到账需要你明确确认。每次调用才会查进度，界面上已有的本地状态不会冒充实时官网查询。

## 验证及当前边界

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_fast_flow.cjs
```

测试全部使用合成材料，覆盖多患者、多语言字段、原币金额、PDF/扫描件、去重、审批失效、附件完整性、并发提交锁、未知结果恢复、补件关联和到账分离。

快速流程以三分钟为目标。材料数量、登录状态、官网表单和上传时间会影响实际耗时。按[快速流程](skills/geoblue-claims/references/fast-path.md)记录本次运行覆盖的阶段。遇到缺失材料或网站不可用时，保存待处理状态并报告卡点。

## 许可证

项目代码采用 Apache License 2.0。完整文本见 [LICENSE](LICENSE)，项目声明见 [NOTICE](NOTICE)。
