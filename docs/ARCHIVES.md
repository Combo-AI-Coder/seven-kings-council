# 存档格式、可信边界与迁移

## format 2 清单

新创世记录携带 archive_format=2。必须存在 genesis.json、secrets.json、initial_state.json、state.json、audit.jsonl、manifest.json；每个完成回合N必须有 command_NN.json、result_NN.json、turn_NN.json、docket_NN.json。未终局还必须有下一回合公报。缺失、额外孤立回合和越界回合文件均失败。

验证从种子独立构造隐藏目标与初始状态，检查冻结源码，再逐条重放审计链。命令必须已规范化；每个快照与重放后的规范内容一致。manifest记录所有上述文件的逐字节SHA-256，所以JSON仅变空白也可被检测。manifest本身以规范JSON产生archive_sha256，不对自身递归哈希；普通私人笔记与锁文件不属于游戏归档清单。

自洽目录不等于可信来源。能改写整个目录的人也能重算所有内部哈希，甚至另开种子。请在不受同一攻击者控制的位置保存初始genesis_commit和关键回合archive_sha256。验同一版本的外部锚点：

~~~sh
seven-kings verify --dir /tmp/k7-play --expected-archive-sha256 <之前可信保存的64位SHA256>
~~~

新回合改变锚点是正常的；不能用第1轮锚点验证第2轮归档。

## 提交与恢复

所有引擎读写使用同一个非阻塞独占锁。冲突命令报busy，而不是排队后悄悄成为下一回合。模板包含expected_audit_tip；同审计头、同规范命令的重试返回原回合回执；同旧审计头、不同命令拒绝。旧裸命令仅为顺序API兼容，不适合跨进程持久重试。

回合先写.transaction/staged完整候选目录并验证，提交决定通过原子替换的commit.json持久化：

- 标记之前失败：正式目录不变，recover丢弃未提交工作区。
- 标记之后失败：recover验证候选目录和每份可见文件是旧版或新版合法字节，再幂等向前完成全部写入。
- 清单最后发布；工作区先原子重命名为清理目录，再删除，避免清理中断误伤已提交回合。
- 任意正式文件被第三方篡改或删除，恢复拒绝，不用工作区替你覆盖历史损坏。

~~~sh
seven-kings recover --dir /tmp/k7-play
seven-kings verify --dir /tmp/k7-play
~~~

支持POSIX flock和Windows msvcrt锁；提交依赖本地文件系统的原子替换与文件fsync。Windows无可移植目录fsync，不承诺完整断电持久性。网络文件系统/同步盘/多主机不是支持的事务环境。不要绕过锁手工编辑运行中的目录。这里的崩溃恢复不等于抵御恶意本地操作者或硬盘损坏。

## 冻结源码与旧档

engine.py、rules.py、__init__.py的联合源码SHA封存在创世记录。代码变动后不能给旧档重签或跳过检查。旧档请继续用原始代码验档，永久保留原始文件与公开锚点。

format 1的合成档要在其原始引擎验证后导出规范命令，再用新引擎在新目录、新创世重放。新引擎不会把format 1原地改成format 2。相同命令可能因新版规则不再合法，届时重放会在创建目标目录前拒绝；不能为了得到相同结局修改历史记录。

format 2可以直接导出不含种子/目标的命令序列：

~~~sh
seven-kings export --dir /tmp/k7-demo > /tmp/k7-commands.json
seven-kings replay --dir /tmp/k7-replay-new --commands-file /tmp/k7-commands.json \
  --seed public-synthetic-demo-hell-1.2-only --game-id REPLAY-DEMO
seven-kings verify --dir /tmp/k7-replay-new
~~~

原版本没有export命令时：先在原代码下verify，再从已验证的audit.jsonl逐条取payload.command，按下面结构写到目录外的新文件。原始数据或字段结构不同则需人工核对，不能声称新版验档已覆盖旧格式。

~~~json
{
  "format": "seven-kings-command-replay-v1",
  "source": {"note": "Original frozen engine verified this synthetic archive"},
  "commands": [
    {"action":{"type":"pass"},"diplomacy":{"type":"none"},"court":{"type":"none"},"reply":"decline","vote":"no","sway":0,"bid":0}
  ]
}
~~~

source是来源说明，不是新引擎对旧引擎的认证；它不能授予绕过验证的能力。replay只验证输入命令在当前代码下可执行，然后建立新实验。不要把真实旧战役复制成公开“合成测试”；不得把改变后的结果回填旧版终局。
