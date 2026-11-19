"""可移植的冻结路线加载与入口。不修改 rules/ 下的冻结文件。"""

from .paths import REPO_ROOT, alias_baidu_keys, dataset_dir, prepare_sys_path

__all__ = ["REPO_ROOT", "alias_baidu_keys", "dataset_dir", "prepare_sys_path"]
