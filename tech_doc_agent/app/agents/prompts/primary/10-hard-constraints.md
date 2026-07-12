硬性约束：
- plan 的最后一个步骤必须是 explanation / examination / summary 中的一个。
- parser 和 relation 是后端助手，不面向用户产出最终回复，它们绝不能作为 plan 的最后一步。
- 如果 plan 中包含 parser 或 relation，后面必须至少跟一个 explanation / examination / summary。
