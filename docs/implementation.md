# 价格、成功条件与接口依据

[返回首页](../README.md) · [配置说明](configuration.md) · [使用与恢复](usage.md)

## 价格和成功的定义

单价以网页使用的千分之一元整数换算，采用未折扣原价作上限，不使用优惠券，也不申请额外付费数据盘。提交前会重新查询主机并取得报价，再检查单卡上限和任务小时总价上限。

创建接口的网页请求结构没有暴露原子价格上限参数，因此报价与创建之间价格变化的竞态无法彻底排除；价格检查是提交前检查。小时总价也不是累计支出预算，不包含账号中其他实例。

“成功”指创建接口返回实例 ID，并且只读查询确认该实例存在；不代表镜像已拉取完成、实例已运行或训练已开始。已有实例不计入本任务，已完成记录也不会因后来关机或释放而自动扣减，适用于一次性抢租。

**停止脚本、达到停止条件或等待超时，只会停止继续租用，已经租到的实例不会自动关机或释放。** 如不再使用，请在 AutoDL 控制台处理；计费以 [AutoDL 计费说明](https://api.autodl.com/docs/price/) 为准。

## 验证和接口依据

```bash
python3 -m unittest discover -s tests -v
```

离线测试覆盖三种策略、CPU 租用、配额、严格低于1元、精确型号匹配、多卡、分页、特殊 CUDA 目录条目、长 Token 错误信息脱敏、报价变化、快速价格筛选及降价后的重新发现、价格合计、库存竞争、目录恢复、限流退避、扫码登录状态机、崩溃后的待确认记录与文件锁；CLI 测试还覆盖 JSON 协议、非交互参数、环境变量认证、扫码图片事件、预览、断点续跑及错误退出码。另用 `python3 -S` 验证不加载第三方包也可运行普通入口。

历史上已用真实登录账号验证地区、型号、镜像、市场库存与报价接口，以及原生扫码登录挑战/等待接口。CLI 整理阶段使用 API 假数据验证，没有额外租用实例。

接口核对日期：**2026-10-22**。普通市场的登录后接口依据当时网站公开前端核对，已完成 CPU 按量实例的市场筛选、镜像选择、报价、创建及存在状态确认；尚未进行付费 GPU 创建验证。网站接口变化时需相应更新，脚本会对未知创建结果保留待核对记录。

| 功能 | 网站请求 |
| --- | --- |
| 地区 | `GET /api/v1/common` 中的 `region_list` |
| GPU 型号 | `GET /api/v1/machine/gpu_type` |
| 基础镜像 | `POST /api/v1/image/all` |
| 普通市场主机 | `POST /api/v1/user/machine/list` |
| 报价 | `POST /api/v1/order/price/preview` |
| 按量创建 | `POST /api/v1/order/instance/create/payg` |
| 确认存在 | `GET /api/v1/instance/exist?instance_uuid=...` |

公开前端来源：[市场模块](https://www.autodl.com/assets/index.ec54d3c7.js)、[创建模块](https://www.autodl.com/assets/index.9040cb9d.js)、[市场接口封装](https://www.autodl.com/assets/service-code.30826265.js)、[报价接口](https://www.autodl.com/assets/getPrice.b8d5d4ee.js)、[基础镜像模块](https://www.autodl.com/assets/system-image.vue_vue&type=style&index=0&lang.d3e67626.js)、[实例接口](https://www.autodl.com/assets/instance.4daa8be5.js)。这些带版本散列的地址可能随网站发布变更。

当前范围是主账号的普通算力市场、NVIDIA GPU 或 CPU / x86、按量计费、基础或私有镜像；未适配子账号、Pro、弹性部署、包年包月、社区镜像和自动关机。官方的 [容器实例 Pro API](https://api.autodl.com/docs/instance_pro_api/) 属于另一个产品，不能直接替换普通市场创建请求。
