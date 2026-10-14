# 学习报告（learning-report）

批次：`{{ batch_id }}`

## 预测准确性
- 样本分母：{{ samples }}（已观察；未测试品不计入）
- Top-N 成功率：{{ top_n_success_rate }}
- precision / recall：{{ precision }} / {{ recall }}

## 高分失败
{{ high_score_failures }}

## 低分成功
{{ low_score_winners }}

## 变量发现
{{ variable_findings }}

## 盲区
{{ blind_spots }}

## 阈值/权重问题
{{ threshold_issues }}

## 校准提案
{{ calibration_proposal }}

## 下批验证
{{ next_batch }}

> 可能因素 ≠ 原因；样本量与选择偏差限制见报告。
