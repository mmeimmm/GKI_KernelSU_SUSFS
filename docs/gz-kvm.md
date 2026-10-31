# 可选 GZ/KVM 共存构建

在 Actions 的 **内核构建 - Android 12 (5.10)** 中，新增 **启用 GZ/KVM 共存（仅 5.10.226，需配套 TEE）** 开关，默认关闭。

关闭时仍执行原来的 22 个版本矩阵、编译命令、依赖处理和打包步骤。开启时单独构建 Android 12 / 5.10.226 / 2024-11，精确 AOSP base 为 `4968e29b7f9287a83b91a18cb497dca5c34f8506`。原有 KernelSU、SUSFS、BBG、ZRAM 等选项照常传入，依赖仓库仍按原流程选取。

GZ 模式使用 kit-09 的 24 文件补丁，SHA256 为 `1e64a07bdebc24ad55234a0d0639f9410a932b0674da86e0d70b2f47519291dc`。该补丁包含共享 EL2 stage-1、协作式 nVHE、FP/timer 和跨大小核缓存寄存器身份修复。配套 TEE 必须支持共享页表/native-pin 服务：FID `0xc600ff10`、magic `0x475a5054`、version 1、特性位包含 `0x5`；kit-08/09 使用的 TEE SHA256 为 `efaba4e4ea3567f9708227a88709bb8b76bc7572bf33aa826c3fac395da59d2c`。

开启 GZ 后增加 full LTO、CFI、4 KiB 页、39 位 VA 及 GZ/nVHE 命令行参数，原有参数保留。IPC 使用原 Droidspaces 补丁的 678 预留槽位；Droidspaces 选项可保持 `off` 或使用 `678`。其他槽位会明确报错，避免改变用户选择或破坏厂商模块布局。

下载原有 **AnyKernel3** 安装包和新增的 **GZ-KVM-…-Report**。GZ 模式通过 AnyKernel3 使用设备当前 boot 的 ramdisk，不生成原流程的通用 GKI ramdisk boot 镜像。关闭 GZ 时，原 Boot 打包逻辑照常执行。

Report 保存实际生成的配置、Image、System.map、Module.symvers、源码身份、编译日志及 ABI 核对结果。检查包含 24 个补丁文件、实际 GZ/KVM 链接符号、7219 个原导出符号 CRC 和 IPC DWARF 布局；失败时不会留下成功验收标记。每次 CI 成品的 `device_tested` 保持 `false`，需要按其实际 Image/配置/依赖版本重新实机验证，不能继承本地 kit-09 的结果。

本次接入只修改 Android 12 入口和共享 build 工作流，增加带开关的步骤；原步骤内容与其他工作流保持不变。关闭开关的结构对比、52 个 shell 语法块、6 项配置/失败门禁检查以及现有 kit-09 真实产物核对已通过。当前接入尚未运行新的 GitHub Actions 完整构建。
