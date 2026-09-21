# Offline speech third-party notices

The offline speech bundle is redistributed for Windows x64 and CPython 3.11. Exact SHA-256 values for every
model and wheel artifact are recorded in `assets/speech/SHA256SUMS`.

## sherpa-onnx and sherpa-onnx-core

- Version: 1.13.8
- Artifacts: `sherpa_onnx-1.13.8-cp311-cp311-win_amd64.whl` and
  `sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl`
- Source: https://github.com/k2-fsa/sherpa-onnx/tree/v1.13.8
- Package source: https://pypi.org/project/sherpa-onnx/1.13.8/
- License: Apache License 2.0
- License file: `licenses/sherpa-onnx-APACHE-2.0.txt`

The core wheel contains ONNX Runtime 1.28.2 native binaries, as pinned by the sherpa-onnx 1.13.8 Windows x64
build configuration.

## ONNX Runtime

- Version: 1.28.2, bundled inside `sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl`
- Source: https://github.com/microsoft/onnxruntime/tree/v1.28.2
- License: MIT
- License file: `licenses/onnxruntime-MIT.txt`
- Third-party notices: `licenses/onnxruntime-ThirdPartyNotices.txt`

## MeloTTS Chinese converted model

- Revision: `a0d5c6a264c0ef92d70d8661d8cc502d79627cd6`
- Artifacts: `model.int8.onnx`, `tokens.txt`, `lexicon.txt`, four rule FSTs, and the bundled `dict` tree
- Converted model source:
  https://huggingface.co/csukuangfj/vits-melo-tts-zh_en/tree/a0d5c6a264c0ef92d70d8661d8cc502d79627cd6
- Original project: https://github.com/myshell-ai/MeloTTS
- License: MIT
- License file: `licenses/melo-tts-model-MIT.txt`

## cppjieba dictionaries

- Version: 5.0.5
- Artifacts: the files under `assets/speech/melo-tts-zh_en-int8/dict`
- Source: https://github.com/yanyiwu/cppjieba/tree/v5.0.5
- Copyright: Copyright (c) 2013
- License: MIT
- License file: `licenses/cppjieba-MIT.txt`

## python-sounddevice

- Version: 0.5.3
- Artifact: `sounddevice-0.5.3-py3-none-win_amd64.whl`
- Source: https://github.com/spatialaudio/python-sounddevice/tree/0.5.3
- Package source: https://pypi.org/project/sounddevice/0.5.3/
- License: MIT
- License file: `licenses/python-sounddevice-MIT.txt`

The Windows wheel includes PortAudio binaries. Its bundled attribution is preserved in
`licenses/portaudio-binaries-README.md`; the PortAudio MIT license is in `licenses/portaudio-MIT.txt`.

## CFFI

- Version: 2.1.1
- Artifact: `cffi-2.1.1-cp311-cp311-win_amd64.whl`
- Source: https://github.com/python-cffi/cffi/tree/v2.1.1
- Package source: https://pypi.org/project/cffi/2.1.1/
- License: MIT No Attribution (`MIT-0`), as declared by the 2.1.1 wheel metadata
- License file: `licenses/cffi-MIT-0.txt`

## pycparser

- Version: 3.0
- Artifact: `pycparser-3.0-py3-none-any.whl`
- Source: https://github.com/eliben/pycparser/tree/release_v3.0
- Package source: https://pypi.org/project/pycparser/3.0/
- License: BSD 3-Clause
- License file: `licenses/pycparser-BSD-3-Clause.txt`
