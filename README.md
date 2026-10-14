# 电商作业平台

Windows 本地工作台：建立商品档案、登记来源和货源、预测、审阅图视频、准备上架方案、记录指标和复盘。默认仅在本机运行。

## 先创建自己的账号

1. 打开项目文件夹，双击 **创建账号.cmd**。
2. 输入自己想用的用户名，按回车；输入密码，再输入一次确认。密码输入时不显示字符，也不显示星号，这是正常现象。
3. 出现“账号已创建”后，双击 **打开电商作业平台.vbs**，打开 http://127.0.0.1:8510 登录。

账号是这个本地平台的账号，不是 GitHub、淘宝或抖音账号。没有通用默认密码。数据库已有同名账号时，请换一个用户名；本次升级不会重置已有账号。

本机已有运行环境时无需重新安装。首次在新电脑安装才执行：

```powershell
uv venv --python 3.11.15 .venv
uv pip install -p .venv -r requirements.lock.txt
.venv\Scripts\python.exe -m app.cli init-db --data-dir runtime
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create-shortcuts.ps1
```

移动项目目录后也需要重新运行 `scripts/create-shortcuts.ps1`，旧快捷方式不会自动跟随目录变化。视频校验需要已有的 ffmpeg/ffprobe；缺少时明确阻止视频入库，不假装校验成功。

## 平台怎么用

### 配置自己的模型服务

登录后打开顶部 **服务连接**。每位使用者自行填写自己的 API Key 和模型/推理接入点 ID；GitHub 源码不提供共享密钥，不读取开发者的环境变量密钥，不回退使用其他用户的账号。

- **LibTV / Seedance**：点击“连接 LibTV”，在官方页面登录自己的账号并授权。返回后可以检查连接、查询项目和 Seedance 模型。请从 `http://127.0.0.1:8510` 打开平台。
- **API Key**：选择 TypeSafe、火山方舟 / Seedance 或 OpenAI 图片服务，填写该服务的 Key 和实际模型 ID，再保存。火山方舟 API Key 与 LibTV 授权是两种独立接入方式。
- 密钥按当前平台用户隔离，并由当前 Windows 用户的 DPAPI 加密，存放于 `runtime/private`；接口不回显密钥，不放进浏览器本地存储，也不包含在业务备份中。删除配置仅删除本机副本，服务商端吊销 API Key 需到其控制台操作。
- 换电脑、换 Windows 用户或授权过期后重新配置。不要分享整个 runtime 文件夹。当前为 Windows 本地应用，业务档案仍属于本地工作台；不是已经实现业务数据租户隔离的公网多人 SaaS。

**当前边界：** LibTV 为独立只读授权入口，其真实浏览器授权仍需使用者完成验证。制作调用、费用核算、成品回填及渠道发布仍待继续接通，不能把“已配置”理解为“可以生成”。TypeSafe 已有证据/关键词审核、方案审批、任务适配与人工处理流程，但真实收费门禁仍关闭，详见下节。

### TypeSafe 影子审核

顶部 **AI 辅助审核** 可选择商品及证据，建立卖点/SKU 或关键词检查方案。展开方案核对实际发送文本；人工结果有理由、版本和历史，补证据后可以编辑资料重新复查。模型判断不改变事实、不批准素材或发布。

目前已通过本地模拟执行验证，**生产真实 TypeSafe 调用尚不能使用**：官方接口中没有核验到单次金额硬上限及账单核对合同，费用门禁不会凭手填预算放开。后续需先补齐服务商费用能力与真实联调；本轮没有真实付费调用。完整操作和限制见 [TypeSafe 第一阶段接入说明](docs/TypeSafe第一阶段接入说明.md)。

1. **今日工作 → 办理作业**：新建商品。只是熟悉界面时选“练习用模拟资料”；真实选品选“真实商品资料”。可以创建本周批次并把商品加入批次。
2. **商品档案**：选择商品，填写售价、拿货价、需求和货源样本数。登记来源链接、观察时间、证据说明，再录入供应商、SKU、规格及报价。拿货价不确定就保留未知或估算，不要为了通过而改成已核验。
3. 点击 **保存事实 → 生成预测**。WAIT 表示需要补资料；TEST 只表示当前规则允许准备测试，不代表已经验证会盈利。修改事实后应重新预测，旧预测仍保留。
4. **图与视频**：上传素材，按 MAIN（主图）、SKU（规格图）、DETAIL（详情图）登记用途。SKU 名称必须与货源一致；打开预览检查后，逐用途审阅。相同图片用于另一个商品，需要单独审阅。
5. **上架启动**：先看缺口，再保存方案并核对账号、标题、价格、库存和素材。内容审批与费用审批独立。当前生产配置没有接通真实写入/制作提供方，点击执行会明确阻断，不会自动在淘宝、抖音上架，也不会产生真实制作费用。可按准备的内容在平台后台手工操作，保留真实回执。
6. **经营复盘**：记录真实账号、渠道、观察窗口、曝光、点击和订单；保存后复制指标快照标识，创建复盘时绑定原预测和指标。支持证据栏填写本商品证据/指标标识；不能只写一句猜测当证据。未来窗口、少于配置观察周期（默认至少3天）、样本不足、模拟数据等会保留 INSUFFICIENT_DATA。

页面顶部的“当前商品”会在同一浏览器会话中保留。创建/保存操作后的提示位于“办理作业”区域；上面的历史列表刷新页面后更新。

## 启停、备份和恢复

- 双击启动快捷方式可重复运行；已有网页进程不会重复创建，缺少 worker 时会补起。
- 双击停止快捷方式；停止失败会报告错误，不会假报“已停止”。
- 升级前或日常备份使用下面的命令。每次选择新的目录，避免覆盖旧备份。

```powershell
.venv\Scripts\python.exe -m app.cli backup --data-dir runtime --output work\backup-20260919
.venv\Scripts\python.exe -m app.cli restore --backup work\backup-20260919 --data-dir work\restored-20260919
```

恢复到新目录，不覆盖原始 runtime。备份包含数据库及其引用的素材/本地证据，并校验 hash；不包含 private 登录凭证。旧本地证据缺文件 hash 时需重新登记。

恢复后的任何未决外部请求都需要先核对，包括备份时尚未提交的请求，因为原库可能在备份后已经执行。登录后通过 `/docs` 中 `/api/v1/recovery` 查看待核对记录；有提供方请求标识可调用 reconcile；无标识时在提供方后台查证，登记本商品事实证据后使用 manual-reconcile。必须填写实际费用与凭证，不能拿预算代替实际账单。全部核对完成才解除恢复阻断；未知请求不会自动重发。

## PM 数据与版本

```powershell
.venv\Scripts\python.exe -m app.cli import-baseline --data-dir runtime --source docs/handoff/sources/strategy-20260914
.venv\Scripts\python.exe -m app.cli verify-baseline --data-dir runtime
.venv\Scripts\python.exe -m app.cli score --data-dir runtime
.venv\Scripts\python.exe -m app.cli export-candidates --data-dir runtime --output work\candidates.csv
.venv\Scripts\python.exe -m app.cli import-outcomes --data-dir runtime --input work\outcomes.csv
.venv\Scripts\python.exe -m app.cli backtest --data-dir runtime
.venv\Scripts\python.exe -m app.cli learn --data-dir runtime --minimum-margin 8
```

CSV 按 `docs/handoff/contracts/pm-fields.json` 的字段合同准备；对象字段为 JSON，空单元格保留 null，真实负利润允许，负费用/非法计数不允许。Outcome 的商品、策略版本和证据引用必须存在且匹配。重复导入同一内容不叠加样本。

学习只生成提案。没有真实可判定样本、明确候选配置或对照结果时，不能接受为可运行版本。有有效提案时按 learn 打印的命令接受，再执行 `select-model --data-dir runtime --version-id <版本ID> --owner-id <现有账号ID>` 明确选择；接受不等于自动启用，历史预测不变。现阶段可执行校准针对旧规则引擎的最低贡献利润门槛，并非完整的任意权重学习系统，结果仍是 PROVISIONAL。

## 验证与边界

详细修改索引与实测结果见 [R2修正与验证报告](docs/R2修正与验证报告.md)。默认测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q
```

G01（网页/API）、G02（批次）、G03（PM数据与学习）是功能任务编号，不能代替下列原规划关口：

| 关口 | 定义及当前证据 |
|---|---|
| G0 | 范围和合同准备；以原规划为准 |
| G1 | 离线软件验证；见本次测试报告 |
| G2 | 本地模拟流程；五商品 HTTP 链路有隔离验证，浏览器抽查不等同完整人工验收 |
| G3 | 真实只读采集；本次未验证 |
| G4 | 真实制作及确认发布；本次未验证 |
| G5 | 真实经营观察；本次未验证 |

本次代码修正不代表 G3–G5 已通过，不声称真实渠道已接通、已制作发布或已盈利。TypeSafe 参考测试也不代表本项目真实接入验收。


## 表格导入与导出

平台左侧“表格导入”支持 Excel .xlsx、CSV、TSV，并提供在线填写模板与 CSV 导出；“使用教程”包含字段说明。详见 [表格导入与导出说明](docs/表格导入与导出使用说明.md)。
