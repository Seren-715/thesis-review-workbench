# 论文审阅工作台桌面客户端

0.2.0 独立版内含 Electron、隔离 Python 运行时、前端和通用规则。安装后无需另装 Python 或 Node.js；不包含私人论文、历史批注或 API Key。

首次启动自动初始化用户数据目录下的 `engine` 工作区，可直接上传 DOCX/PDF 进行本地检查。模型审阅需主动开启并填写 Key；Word 分页与 SVN 采集依赖可选组件。内置规则不代替学校规范确认。也可切换已有工程，兼容 `.venv/Scripts/python.exe`。

客户端在空闲端口启动自有后端，并用随机会话密钥验证服务身份；不会复用或关闭已有服务。正在执行任务时关闭窗口会提示处理方式，已有审阅记录和论文原件保留。客户端不会通过原稿内容执行系统命令。

构建：先在已配置源码工程执行 `.venv/Scripts/python.exe -B build_standalone.py` 生成 `output/standalone_20261022/engine`，然后此目录执行 `npm ci`、`npm run dist`。新版本应使用新的 release_id 和输出目录，不能覆盖用户工作区。构建缓存使用普通本地磁盘的 `ELECTRON_BUILDER_CACHE`。

开发运行 `npm start` 时需手动选择已有工程。成品支持 `--profile-dir=<绝对目录>` 使用独立配置目录，可用于隔离验收。用户记录不随程序升级自动覆盖。

浏览器独立启动时，请打开终端输出的完整会话链接；页面初始化后会移除链接中的 token，并建立 HttpOnly 会话 Cookie。论文、报告及任务接口的 GET 请求需要会话凭据。
