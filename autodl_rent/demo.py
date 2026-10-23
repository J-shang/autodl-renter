"""An explicitly offline API double shared by the CLI demo and unit tests."""
from copy import deepcopy
from decimal import Decimal


def demo_machine(model="RTX 4090", identifier="demo-4090", price=2000, region="west-B"):
    return {
        "machine_id": identifier, "gpu_name": model, "region_sign": region, "region_name": "西北B区",
        "gpu_idle_num": 8, "gpu_number": 8, "max_instance_num": 8, "binding_instance_num": 0,
        "on_line": 2, "rent_mode": "Base1", "chip_corp": "nvidia", "cpu_arch": "x86",
        "gpu_memory": 24 * 1024**3, "cpu_per_gpu": 16, "mem_per_gpu": 64 * 1024**3,
        "highest_cuda_version": "12.8", "machine_sku_info": [{"type": "payg", "current_price": price}],
    }


class DemoAPI:
    is_simulation = True

    def __init__(self):
        self.offers = [demo_machine(), demo_machine("RTX 3090", "demo-3090", 1000)]
        self.created = []
        self.queries = []

    def regions(self):
        return [{"region_name": "西北B区", "region_sign": "west-B"},
                {"region_name": "北京B区", "region_sign": "bj-B1"}]

    def gpus(self):
        return [{"gpu_name": name, "gpu_series_type": series, "chip_corp": "nvidia"} for name, series in (
            ("RTX 4090", "NVIDIA-Ada"), ("RTX 3090", "NVIDIA-Ampere"), ("RTX 4090D", "NVIDIA-Ada"),
            ("CPU", "CPU"), ("CPU-close-HT", "CPU"))]

    def images(self):
        return [{"uuid": "demo-image", "cuda_version": "12.8", "chip_corp": "nvidia", "cpu_arch": "x86"}]

    def machines(self, target, regions, page, page_size, machine_id=""):
        self.queries.append((target.name, page, page_size, machine_id))
        rows = [deepcopy(m) for m in self.offers if m["gpu_name"] in target.gpu_models
                and m["region_sign"] in regions and (not machine_id or m["machine_id"] == machine_id)]
        start = (page - 1) * page_size
        return {"list": rows[start:start + page_size], "result_total": len(rows)}

    def quote(self, machine_id, gpu_count):
        machine = next(m for m in self.offers if m["machine_id"] == machine_id)
        return Decimal(machine["machine_sku_info"][0]["current_price"]) / 1000 * gpu_count

    def create(self, machine_id, gpu_count, image, name):
        identifier = f"demo-instance-{len(self.created) + 1}"
        self.created.append({"uuid": identifier, "machine_id": machine_id, "gpu_count": gpu_count, "name": name})
        return identifier

    def exists(self, instance_uuid):
        return any(i["uuid"] == instance_uuid for i in self.created)
