from setuptools import setup, find_packages
import io
import os

version = os.environ.get('RELEASE_VERSION', '0.1.9'
'').lstrip('v')

setup(
    name="chatgpt-mirai-qq-bot-scheduler",
    version=version,
    packages=find_packages(),
    include_package_data=True,  # 这行很重要
    package_data={
        "scheduler": ["example/*.yaml", "example/*.yml"],
    },
    install_requires=[
        "playwright","trafilatura","lxml_html_clean",
    ],
    entry_points={
        'chatgpt_mirai.plugins': [
            'scheduler = scheduler:SchedulerPlugin'
        ]
    },
    author="chuanSir",
    author_email="416448943@qq.com",

    description="定时任务 for lss233/chatgpt-mirai-qq-bot，提供聚合block实现定时任务的增删查(同时也可以配置其他安装的block实现自动调用)",
    long_description=io.open("README.md", encoding='utf-8').read(),
    long_description_content_type="text/markdown",
    url="https://github.com/chuanSir123/scheduler",
    classifiers=[
        "Programming Language :: Python :: 3",
        'License :: OSI Approved :: GNU Affero General Public License v3',
        "Operating System :: OS Independent",
    ],
    project_urls={
        "Bug Tracker": "https://github.com/chuanSir123/scheduler/issues",
        "Documentation": "https://github.com/chuanSir123/scheduler/wiki",
        "Source Code": "https://github.com/chuanSir123/scheduler",
    },
    python_requires=">=3.8",
)
