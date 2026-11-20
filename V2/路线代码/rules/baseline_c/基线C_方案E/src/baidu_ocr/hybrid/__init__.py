"""blind20 混合路线（B 打底 + OpenAI 视觉模型）的区域选择、裁图、调用（默认 dry-run）、合并与计分。

本包不在导入时做任何网络请求。外发只在 vlm.run_live() 中发生，且需同时满足：
config 中 vlm.allow_external 为 true、命令行 --live、环境变量里有 key（只检查有无，不打印）。
"""
