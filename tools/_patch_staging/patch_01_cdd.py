# -*- coding: utf-8 -*-
# 补丁 1：kaoyan-progress-sync 修 cd /d（cmd 语法在 pwsh 下失败）+ 版本记录
EDITS = [
    {
        "file": r"C:\Users\92534\.qoderworkcn\skills\kaoyan-progress-sync\SKILL.md",
        "replacements": [
            {
                "old": "```bash\ncd /d E:\\NPEE\\src\npython gap_analysis.py\npython daily_planner.py\npython generate_dashboard.py\n```",
                "new": "```bash\npython E:\\NPEE\\src\\gap_analysis.py\npython E:\\NPEE\\src\\daily_planner.py\npython E:\\NPEE\\src\\generate_dashboard.py\n```",
                "all": False,
            },
            {
                "old": "version: 1.0.0\n---\n\n# 考研学习进度同步",
                "new": "version: 1.1.0\n---\n\n# 考研学习进度同步",
                "all": False,
            },
        ],
    },
]
