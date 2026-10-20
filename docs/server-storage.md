# 服务器数据盘分工

2026-10-20 已将下载缓存和发布备份迁移到现有数据盘。两块盘均通过 `/etc/fstab` 持久挂载，原路径保留为符号链接，现有工具继续使用原路径。

| 原路径 | 实际存储位置 |
| --- | --- |
| `/home/ubuntu/.cache/pip` | `/data0809/qiubot/storage/cache/pip` |
| `/home/ubuntu/.npm/_cacache` | `/data0923/qiubot/storage/cache/npm-cacache` |
| `/opt/qiubot/deploy-backups` | `/data0923/qiubot/storage/backups/deploy-backups` |
| `/opt/qiubot/backups` | `/data0923/qiubot/storage/backups/qiubot-backups` |
| `/opt/qiubot/backup` | `/data0923/qiubot/storage/backups/qiubot-legacy-backup` |
| `/opt/qiubot-deploy-backups` | `/data0923/qiubot/storage/backups/legacy-deploy-backups` |

这次迁移释放系统盘约 5 GB，使用率由 79% 降至 65%。所有备份均保留，复制时保留权限、所有者、时间戳和内部符号链接，通过 rsync 内容校验后原子切换原路径，再移除已校验的旧副本。已按各目录原所有者验证写入权限。

正式数据库、代码、已安装运行环境和活跃验收数据库维持现有位置；迁移没有重启网站、机器人或验收服务。磁盘迁移不改变查询算法，也不增加物理内存。

## 后续维护

- 缓存和备份继续使用表中的原路径；不要把兼容符号链接覆盖为普通目录。
- 运行相关工具前，确认 `/data0809`、`/data0923` 已挂载；使用中不要卸载数据盘。
- 服务器说明位于 `/data0923/qiubot/storage/README.md`，迁移与验证记录位于 `/data0923/qiubot/storage/maintenance/storage-20261020-122425/`。
- 不将服务器环境变量、数据库、卡图、下载缓存、备份副本或迁移清单中的业务数据提交到 Git。

回迁时先确认没有写入进程，并检查系统盘可用空间；将目标内容复制至原路径旁的新目录，校验内容后原子交换该目录与原符号链接。完成读写与服务检查前，保留数据盘副本。
