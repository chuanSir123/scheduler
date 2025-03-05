import sqlite3
import json
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass
import os
@dataclass
class ScheduledTask:
    id: str
    cron: str  # cron 表达式，一次性任务为空字符串
    task_content: str  # 任务内容/消息内容
    chat_id: str  # 关联的聊天ID
    created_at: datetime
    next_run_time: Optional[datetime] = None
    last_run_time: Optional[datetime] = None
    is_one_time: bool = False  # 新增字段，标识是否为一次性任务

class TaskStorage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    cron TEXT NOT NULL,
                    task_content TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    next_run_time TIMESTAMP,
                    last_run_time TIMESTAMP,
                    is_one_time BOOLEAN DEFAULT 0
                )
            ''')

    def save_task(self, task: ScheduledTask):
        """保存或更新任务"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO scheduled_tasks
                (id, cron, task_content, chat_id, created_at, next_run_time, last_run_time, is_one_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.id,
                task.cron,
                task.task_content,
                task.chat_id,
                task.created_at.isoformat(),
                task.next_run_time.isoformat() if task.next_run_time else None,
                task.last_run_time.isoformat() if task.last_run_time else None,
                task.is_one_time
            ))

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取指定任务"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT * FROM scheduled_tasks WHERE id = ?', (task_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_task(row)
        return None

    def get_all_tasks(self, chat_id: str = None) -> List[ScheduledTask]:
        """获取所有任务，可选择按chat_id过滤"""
        tasks = []
        with sqlite3.connect(self.db_path) as conn:
            if chat_id:
                cursor = conn.execute('SELECT * FROM scheduled_tasks WHERE chat_id = ?', (chat_id,))
            else:
                cursor = conn.execute('SELECT * FROM scheduled_tasks')
            for row in cursor:
                tasks.append(self._row_to_task(row))
        return tasks

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('DELETE FROM scheduled_tasks WHERE id = ?', (task_id,))
            return cursor.rowcount > 0

    def delete_all_task(self, chat_id: str = None) -> bool:
        """删除任务，可选择按chat_id删除"""
        with sqlite3.connect(self.db_path) as conn:
            if chat_id:
                cursor = conn.execute('DELETE FROM scheduled_tasks WHERE chat_id = ?', (chat_id,))
            else:
                cursor = conn.execute('DELETE FROM scheduled_tasks')
            return True

    def _row_to_task(self, row) -> ScheduledTask:
        """将数据库行转换为任务对象"""
        return ScheduledTask(
            id=row[0],
            cron=row[1],
            task_content=row[2],
            chat_id=row[3],
            created_at=datetime.fromisoformat(row[4]) if isinstance(row[4], str) else datetime.now(),
            next_run_time=datetime.fromisoformat(row[5]) if row[5] and isinstance(row[5], str) else None,
            last_run_time=datetime.fromisoformat(row[6]) if row[6] and isinstance(row[6], str) else None,
            is_one_time=bool(row[7]) if len(row) > 7 else False
        )
