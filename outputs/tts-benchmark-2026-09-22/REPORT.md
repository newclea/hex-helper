# 本机实时 TTS 对比（2026-09-22）

建议：优先验证接入 MeloTTS FP32 + 8 线程。它保留同模型系列声音，在本轮 CPU 测试中达到 RTF < 0.5，六个样本无需额外等待后续分句。当前生产模型未切换。

## 条件与方法

- Windows，Intel i9-13900HX（24 核 / 32 逻辑处理器）；另有 RTX 4060 Laptop 8GB，本次没有使用 GPU。
- sherpa-onnx 1.13.8，NumPy 1.26.4，CPU provider，sid=0，speed=1.0。
- 每配置 3 种文本、各 2 次：23 字选人推荐、16 字海克斯摘要、35 字欢迎语。每种配置单独启动进程，模型常驻；没有读取音频缓存。
- 模仿当前应用按标点切句，callback 接收音频。合成计时不含加载模型、保存 WAV、声卡启动。
- 首段时间是生成首个音频块的时间，不是用户实际听到声音的时间。
- RTF = 合成秒数 / 音频秒数，<1 快于播放；建议为游戏负载留出余量，目标 <=0.5。
- continuous_start_seconds 根据各块到达时间与长度推算避免断供所需的最早开播时间；这是离线时间线推算，不是声卡实测。
- 顺序执行，未固定 CPU 频率或进程亲和性；没有主动启动游戏压测，也未控制用户后台负载。样本少，结果不是性能保证。

## 汇总

| 配置 | 模型 MiB | 加载秒 | 23 字推荐合成秒（均值） | 汉字/秒（全部文本） | 加权 RTF | 首段秒范围 |
|---|---:|---:|---:|---:|---:|---:|
| melo-int8 | 51.0 | 2.38 | 14.04 | 1.8 | 2.781 | 2.99–5.21 |
| melo-fp32-t1 | 162.5 | 4.54 | 5.55 | 4.9 | 1.006 | 0.90–2.25 |
| melo-fp32-t2 | 162.5 | 3.69 | 4.80 | 5.0 | 0.986 | 1.00–1.77 |
| melo-fp32 | 162.5 | 4.37 | 3.08 | 8.0 | 0.614 | 0.63–1.11 |
| melo-fp32-t8 | 162.5 | 3.99 | 1.83 | 13.3 | 0.369 | 0.39–0.65 |
| piper-xiaoya-fp32 | 60.3 | 1.61 | 0.50 | 41.0 | 0.093 | 0.13–0.22 |

## 结论与取舍

1. 当前 INT8 在这条 CPU 推理路径明显慢于 FP32。本轮支持更换精度版本，不能泛化为所有 INT8 模型都慢。
2. FP32 的线程表现与 INT8 不同：8 线程达到目标；之前 INT8 的 4 线程结论不能套用到 FP32。
3. FP32 模型约 162.5 MiB，比当前 INT8 大约 111.5 MiB；正式切换需更新模型路径、离线校验清单，并为新模型重新生成固定音频缓存。
4. Piper 小雅最快，但会改变音色，采样率从 44.1kHz 改为 22.05kHz。下载包 MODEL_CARD 标注训练数据 Non-commercial use；本轮只作为研究候选，没有放入正式包。
5. WAV 样本已生成并检查为非空、非静音，没有做主观听感评分或人工逐字发音验收。请对照样本检查妮蔻、海克斯等专名。

## 研究但未实测的路线

- CosyVoice：官方支持文本输入/音频输出双向流式，以及 NVIDIA 推理部署；本机有 GPU，但本次 CPU 已达标，未安装 CosyVoice，也没有 GPU/游戏帧率测试。官方延迟宣传不作为本机数据。
- Azure Speech：官方支持输入文本流和输出音频流；本次未接云服务、未产生调用费用，没有网络延迟实测。

## 产物与复跑

- `benchmark.py`：基准脚本。
- `reproduce.ps1`：使用已下载模型和现有独立 Python 运行依赖，顺序复跑。
- `results/*.json`：逐次数据，包含模型 SHA-256、每个音频块到达时间、RTF、RMS。
- `results/*-champions.wav`：同一句推荐语的音色对照；另有 greeting、hex 样本。
- `playback_probe.py`：可选真实声卡验证脚本，本轮未执行，避免干扰用户进行中的游戏。

## 官方来源

- [MeloTTS 模型与下载](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html#vits-melo-tts-zh-en-chinese-english-1-speaker)
- [小雅模型与 sherpa 调用配置](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese/vits-piper-zh_CN-xiao_ya-medium.html)
- [INT8 慢于 FP32 的既有问题报告，非本机结论](https://github.com/k2-fsa/sherpa-onnx/issues/575)
- [CosyVoice 官方仓库](https://github.com/QwenAudio/CosyVoice)
- [Azure 流式与低延迟合成](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-lower-speech-synthesis-latency)


## 后续生产落地

FP32/8 线程已接入，资源迁至 `assets/speech/melo-tts-zh_en/`，固定音频已重新生成。
缓存开场与后台模型加载并行。`pipeline_probe.py` 使用真实模型与实时静音输出，
ready 0.613 秒、动态首段 0.644 秒，详见 `results/pipeline-probe.json`；不等同声卡播放测试。
