from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import uuid
from typing import Optional, List
from .storage import TaskStorage,ScheduledTask
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.logger import get_logger
import asyncio
from aiocqhttp import Event
from kirara_ai.im.adapter import IMAdapter
from kirara_ai.im.manager import IMManager
from kirara_ai.im.message import IMMessage, MessageElement, TextMessage
from kirara_ai.im.sender import ChatSender

logger = get_logger("TaskScheduler")


class TaskScheduler:
    def __init__(self, storage: TaskStorage, adapter: IMAdapter):
        self.storage = storage
        self.scheduler = AsyncIOScheduler()
        self.adapter = adapter


    def start(self):
        """启动调度器"""
        logger.debug(f"Scheduler running state before start: {self.scheduler.running}")
        if not self.scheduler.running:
            try:
                # 尝试获取现有的事件循环
                try:
                    loop = asyncio.get_running_loop()
                    # 如果有运行中的事件循环，直接启动调度器并加载任务
                    self.scheduler.start()
                    asyncio.create_task(self.load_tasks())
                except RuntimeError:
                    # 如果没有运行中的事件循环，创建一个新的后台线程来运行调度器
                    import threading
                    def run_scheduler():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            # 先运行事件循环
                            loop.run_until_complete(self._start_scheduler())
                            loop.run_until_complete(self.load_tasks())
                            loop.run_forever()
                        except Exception as e:
                            logger.error(f"Error in scheduler thread: {str(e)}")
                        finally:
                            loop.close()

                    thread = threading.Thread(target=run_scheduler, daemon=True)
                    thread.start()

                logger.info("Scheduler started successfully")
            except Exception as e:
                logger.error(f"Failed to start scheduler: {str(e)}")
                raise
        else:
            logger.info("Scheduler was already running")

    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()

    async def create_task(self, cron: str, task_content: str, target: Optional[ChatSender] = None) -> ScheduledTask:
        """创建定时任务"""
        logger.debug(f"创建定时任务：{str(target)}")
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            cron=cron,
            task_content=task_content,
            chat_id=str(target) if target else None,
            created_at=datetime.now()
        )

        try:
            # 创建 CronTrigger
            trigger = CronTrigger.from_crontab(cron)
            logger.info(f"Creating task: {task.id} with cron: {cron}")

            # 添加到调度器
            job = self.scheduler.add_job(
                self._execute_task,
                trigger,
                args=[task],
                id=task.id
            )

            # 验证任务是否成功添加到调度器
            if not self.scheduler.get_job(task.id):
                logger.error(f"Task {task.id} was not properly added to scheduler")
                raise Exception("Failed to add task to scheduler")

            logger.info(f"Task {task.id} successfully added to scheduler")

            # 获取下次运行时间
            task.next_run_time = trigger.get_next_fire_time(None, datetime.now())
            if task.next_run_time is None:
                logger.warning(f"Task {task.id} has no next run time, check cron expression: {cron}")
            else:
                logger.debug(f"Task {task.id} next run time: {task.next_run_time}")

            self.storage.save_task(task)
            return task

        except Exception as e:
            logger.error(f"Failed to create task: {str(e)}", exc_info=True)
            raise

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务信息"""
        return self.storage.get_task(task_id)

    def get_all_tasks(self, chat_id: str = None) -> List[ScheduledTask]:
        """获取所有任务"""
        return self.storage.get_all_tasks(chat_id)

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        if self.scheduler.get_job(task_id):
            self.scheduler.remove_job(task_id)
        return self.storage.delete_task(task_id)

    def delete_all_task(self, chat_id: str = None) -> bool:
        if chat_id:
            tasks = self.storage.get_all_tasks(chat_id)
        else:
            tasks = self.storage.get_all_tasks()

        for task in tasks:
            if self.scheduler.get_job(task.id):
                self.scheduler.remove_job(task.id)
        """删除任务"""
        return self.storage.delete_all_task(chat_id)

    async def _execute_task(self, task: ScheduledTask):
        """执行任务"""
        try:
            logger.info(f"Starting execution of task {task.id}")
            if task.chat_id.startswith("c2c:"):
                target = ChatSender.from_c2c_chat(task.chat_id.split(":")[1], task.chat_id.split(":")[1], {})
            else:
                target = ChatSender.from_group_chat(task.chat_id.split(":")[1], task.chat_id.split(":")[0], {})
            # 直接使用adapter发送消息
            await self.adapter.send_message(
                IMMessage(
                    sender=ChatSender.get_bot_sender(),
                    message_elements=[TextMessage(task.task_content)]
                ),
                target
            )

            logger.info(f"Task {task.id} executed successfully and message sent")

            # 更新任务状态
            task.last_run_time = datetime.now()

            # 获取下次运行时间
            job = self.scheduler.get_job(task.id)
            if job:
                task.next_run_time = job.next_run_time
                logger.info(f"Updated next run time for task {task.id}: {task.next_run_time}")
            else:
                logger.warning(f"Could not find job {task.id} in scheduler")

            self.storage.save_task(task)

        except Exception as e:
            logger.error(f"Error executing task {task.id}: {str(e)}", exc_info=True)

    async def load_tasks(self):
        """加载所有保存的任务"""
        try:
            tasks = self.storage.get_all_tasks()
            logger.info(f"Loading {len(tasks)} tasks from storage")

            for task in tasks:
                try:
                    if task.is_one_time:
                        # 对于一次性任务，如果下次运行时间已过，则跳过
                        if task.next_run_time and task.next_run_time > datetime.now():
                            self.scheduler.add_job(
                                self._execute_one_time_task,
                                'date',
                                run_date=task.next_run_time,
                                args=[task],
                                id=task.id
                            )
                            logger.info(f"Successfully loaded one-time task: {task.id} )")
                        else:
                            # 删除过期的一次性任务
                            self.delete_task(task.id)
                            logger.info(f"Removed expired one-time task: {task.id}")
                    else:
                        # 周期性任务的处理保持不变
                        trigger = CronTrigger.from_crontab(task.cron)
                        job = self.scheduler.add_job(
                            self._execute_task,
                            trigger,
                            args=[task],
                            id=task.id
                        )
                        task.next_run_time = job.next_run_time
                        self.storage.save_task(task)
                        logger.info(f"Successfully loaded periodic task: {task.id} ")

                except Exception as e:
                    logger.error(f"Failed to load task {task.id}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error loading tasks: {str(e)}")
            raise

    async def _start_scheduler(self):
        """在事件循环中启动调度器"""
        self.scheduler.start()

    async def create_one_time_task(self, minutes: int, task_content: str, target: Optional[ChatSender]) -> ScheduledTask:
        """创建一次性定时任务"""
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            cron="",  # 一次性任务不需要cron表达式
            task_content=task_content,
            chat_id=str(target) if target else None,
            created_at=datetime.now(),
            is_one_time=True  # 标记为一次性任务
        )

        try:
            run_time = datetime.now() + timedelta(minutes=minutes)
            logger.info(f"Creating one-time task: {task.id}, run at: {run_time}")

            # 添加到调度器
            job = self.scheduler.add_job(
                self._execute_one_time_task,
                'date',
                run_date=run_time,
                args=[task],
                id=task.id
            )

            # 验证任务是否成功添加到调度器
            if not self.scheduler.get_job(task.id):
                logger.error(f"One-time task {task.id} was not properly added to scheduler")
                raise Exception("Failed to add one-time task to scheduler")

            task.next_run_time = run_time
            self.storage.save_task(task)
            return task

        except Exception as e:
            logger.error(f"Failed to create one-time task: {str(e)}", exc_info=True)
            raise

    async def _execute_one_time_task(self, task: ScheduledTask):
        """执行一次性任务"""
        try:
            await self._execute_task(task)
            # 执行完成后删除任务
            self.delete_task(task.id)
            logger.info(f"One-time task {task.id} completed and removed")
        except Exception as e:
            logger.error(f"Error executing one-time task {task.id}: {str(e)}", exc_info=True)
