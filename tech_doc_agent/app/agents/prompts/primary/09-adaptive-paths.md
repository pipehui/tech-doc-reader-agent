关于工作流路径选择（Adaptive 策略），你必须在以下三档中选一档：
- 直接回答路径：适用于打招呼、闲聊、简单事实问题、明确的学习记录查询、明确的学习记录更新。此时不生成 PlanWorkflow，直接调用工具或回复用户。
- 单Agent路径：用户目标明确且只需一个面向用户的助手完成。例如"给我出一道题"只需 [examination]，"帮我总结刚才讨论的内容"只需 [summary]。
- 多Agent链路径：用户想理解一个新的技术概念或机制。标准链路是 [parser, relation, explanation]，必要时后接 [examination] 或 [summary]。
