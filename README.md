# 七王议会 / Seven Kings Council

七家族、最多20回合的确定性政治策略游戏。你控制曜誓家 A，B–G由封存的确定性AI控制。每回合安排主行动、外交、政治合作与议会投票；第4/8/12/16/20回合竞逐王印。

**开发状态：HELL-1.2，包版本1.2.0a1，尚未发布。** [PR #10](https://github.com/Combo-AI-Coder/seven-kings-council/pull/10) 保持所有者审查门槛。历史 HELL-1.0 / 1.1 / 1.1.1 的战役与封存终局不会被本版重新计算。

## 五分钟试玩（Python 3.10+）

从PR #10功能分支安装；当前 main 不保证包含引擎。以下路径都是新建、可丢弃的合成对局。

~~~sh
git clone --branch feat/hell-1.2-deep-politics https://github.com/Combo-AI-Coder/seven-kings-council.git
cd seven-kings-council
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
seven-kings --help
python -m seven_kings.demo --dir /tmp/k7-demo
seven-kings verify --dir /tmp/k7-demo
seven-kings reveal --dir /tmp/k7-demo
~~~

演示会自动用公开合成种子跑到终局，可能败北或提前硬失败，这仍是完整可验的对局。Windows请将 /tmp/k7-demo 换成自己的新目录。重复运行前换一个空目录，不要删除或覆盖真实档案。

## 自己玩：不用手写整份JSON

~~~sh
seven-kings init --dir /tmp/k7-play --game-id MY-SYNTHETIC --seed public-synthetic-my-first-game
seven-kings docket --dir /tmp/k7-play
seven-kings talk --dir /tmp/k7-play --target D --text '我们该如何合作？'
seven-kings template --dir /tmp/k7-play > /tmp/k7-command.json
# 可直接使用模板；或只修改action、vote等想改变的字段
seven-kings play --dir /tmp/k7-play --command-file /tmp/k7-command.json
seven-kings verify --dir /tmp/k7-play
~~~

每回合重新生成模板，它携带当前审计头，避免把旧命令误用到下一回合。相同模板重试不会重复领取收益；不同的陈旧命令会被拒绝。talk 是只读咨询，不推进回合、不扣资源、不签合同。正式承诺只能在 play 的结构化输入中生效。

第一回合完整裸命令在 [examples/round_01.json](examples/round_01.json)。这兼容旧API，但不含防陈旧提交标识；交互操作优先使用模板。选举回合另需 candidate 为A–G，bid 为0–3。sway 为议会投入，0–2。

~~~json
{
  "action": {"type": "reform"},
  "diplomacy": {"type": "none"},
  "court": {"type": "joint_project", "target": "D"},
  "reply": "decline",
  "vote": "yes",
  "sway": 0,
  "bid": 0
}
~~~

先阅读公报中的资源、议案、事件、盟约期限和报价。行动可能受同回合NPC劫掠影响，起始预算合法并不保证结算时仍有资金；失败会留明确事件。完整规则与政治报价实例见 [中文玩法手册](docs/RULES.md)。

## 保存、验档、恢复与分享

- 游戏目录含私人GM种子和隐藏目标。真实对局请省略 --seed，由引擎生成高熵种子；仅GM持有整目录，玩家只看经过筛选的公报和已结算结果。
- 不要把进行中的目录、secrets.json、完整私密存档或未解封GM数据推送到公开仓库。.gitignore 是辅助，不是安全边界。
- 每次操作前后验证创世承诺、引擎源码SHA、审计链、每轮命令/结果/状态/公报和完整性清单。编辑代码后，旧档会因冻结源码不匹配而拒绝运行；保留原版本来验证原档。
- 若写入被中断，使用 seven-kings recover --dir /tmp/k7-play，再 verify。恢复按已记录提交边界回滚未提交工作或幂等完成已提交回合，不允许修补篡改的历史。
- verify 输出的归档哈希可另存于可信外部位置，日后作为外部锚点复核。目录自带清单或自哈希不能防止攻击者连同种子、创世记录和所有哈希一起整体重写。
- 终局后才允许 reveal。若打算公开一个真实终局，先自行确认没有个人信息及其他秘密；本仓库只提供合成数据。分享可重放资料需冻结源码版本、合成种子、命令、创世承诺和归档锚点。
- 旧合成档迁移采用“原引擎验档 → 导出命令 → 新目录、新创世重放”。这会生成新实验，不替换旧档，也不保证跨版本结果相同。详见 [存档格式与迁移](docs/ARCHIVES.md)。

## 开发与可复现评估

~~~sh
python -m unittest discover -s tests -v
python -m compileall -q src tests
python -m seven_kings.simulation --seeds 100 --output /tmp/k7-balance
~~~

完整评估是100个公开合成种子 × 4种公开信息策略，输出CSV与JSON；快速子集随单测运行。报告包括胜率、完全胜利率、秘密目标达成率、王印集中度、NPC履约率、拒绝/紧张度、共同提名以及刷项目/低价买票对抗策略。配置、观察结果和局限见 [平衡报告](docs/BALANCE.md)。它不证明游戏已经达到理想的地狱难度。

## 文档与许可

- [旧版兼容性对照](docs/COMPATIBILITY.md)
- [变更日志](CHANGELOG.md)
- [交付与发布清单](docs/RELEASE_CHECKLIST.md)
- [MIT许可证](LICENSE)，与原pyproject声明一致

本地目录由GM掌控时，哈希只能提供可复核性，不能阻止有权限的人读到秘密。并发锁面向本机普通文件系统；网络盘、断电耐久性和多主机协调不在支持范围内。
