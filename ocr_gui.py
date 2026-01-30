#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PySide6 OCR图片处理程序
监测 refToPN 目录中的图片，使用 UMI-OCR HTTP API 识别，处理后输出到 CSV
"""

import os
import sys
import csv
import json
import base64
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QGroupBox, QProgressBar,
    QMessageBox, QFileDialog
)
from PySide6.QtCore import Qt, QThread, Signal, QFileSystemWatcher, QTimer
from PySide6.QtGui import QFont, QColor


class OCRProcessor:
    """OCR处理核心逻辑"""
    
    OCR_API_URL = "http://127.0.0.1:1224/api/ocr"
    
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.xml_path = self.base_dir / "refToPartnum.xml"
        self.brand_csv_path = self.base_dir / "brandCode.csv"
        self.output_csv_path = self.base_dir / "ocr_results.csv"
        self.input_dir = self.base_dir / "refToPN"
        self.output_dir = self.base_dir / "refToPartnum-OK"
        
        # 确保输出目录存在
        self.output_dir.mkdir(exist_ok=True)
        
        # 加载配置
        self.regions = self.parse_xml_regions()
        self.brand_mapping = self.load_brand_mapping()
    
    def parse_xml_regions(self) -> dict:
        """解析XML获取OCR区域坐标"""
        regions = {}
        try:
            tree = ET.parse(self.xml_path)
            root = tree.getroot()
            
            for obj in root.findall('object'):
                name = obj.find('name').text
                bndbox = obj.find('bndbox')
                xmin = int(bndbox.find('xmin').text)
                ymin = int(bndbox.find('ymin').text)
                xmax = int(bndbox.find('xmax').text)
                ymax = int(bndbox.find('ymax').text)
                regions[name] = (xmin, ymin, xmax, ymax)
        except Exception as e:
            print(f"解析XML失败: {e}")
        
        return regions
    
    def load_brand_mapping(self) -> dict:
        """加载品牌编码映射表"""
        mapping = {}
        try:
            with open(self.brand_csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    brand_code = row.get('品牌编码', '').strip().upper()
                    universal_brand = row.get('通用品牌', '').strip().upper()
                    if brand_code:
                        mapping[brand_code] = universal_brand
        except Exception as e:
            print(f"加载品牌映射失败: {e}")
        
        return mapping
    
    def load_existing_records(self) -> set:
        """加载已存在的记录用于去重"""
        records = set()
        try:
            if self.output_csv_path.exists():
                with open(self.output_csv_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    next(reader, None)  # 跳过表头
                    for row in reader:
                        if len(row) >= 6:
                            # 使用前6列作为唯一标识（不包含来源文件）
                            records.add(tuple(row[:6]))
        except Exception as e:
            print(f"加载现有记录失败: {e}")
        
        return records
    
    def log(self, message: str, level: str = "info"):
        """输出日志到控制台"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level.upper()}] {message}")
    
    def crop_region(self, image: Image.Image, region: tuple) -> Image.Image:
        """裁剪图片指定区域"""
        xmin, ymin, xmax, ymax = region
        return image.crop((xmin, ymin, xmax, ymax))
    
    def image_to_base64(self, image: Image.Image) -> str:
        """将图片转换为Base64"""
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    
    def call_ocr_api(self, base64_image: str, return_text: bool = False) -> list:
        """调用UMI-OCR HTTP API
        
        Args:
            base64_image: Base64编码的图片
            return_text: 如果为True，返回合并后的单一文本字符串；否则返回文本列表
        """
        try:
            payload = {
                "base64": base64_image,
                "options": {
                    "tbpu.parser": "single_line",
                    "data.format": "text" if return_text else "dict"
                }
            }
            
            response = requests.post(
                self.OCR_API_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            if result.get('code') == 100:
                data = result.get('data', [])
                
                if return_text:
                    # 返回纯文本
                    return data if isinstance(data, str) else ''
                else:
                    # 提取文本并按Y坐标排序
                    texts = []
                    for item in data:
                        text = item.get('text', '').strip()
                        box = item.get('box', [[0, 0]])
                        y_pos = box[0][1] if box else 0
                        texts.append((y_pos, text))
                    
                    # 按Y坐标排序后返回文本列表
                    texts.sort(key=lambda x: x[0])
                    return [t[1] for t in texts if t[1]]
            elif result.get('code') == 101:
                return '' if return_text else []  # 无文本
            else:
                print(f"OCR识别失败: {result.get('data', '未知错误')}")
                return '' if return_text else []
                
        except requests.exceptions.RequestException as e:
            print(f"OCR API调用失败: {e}")
            return '' if return_text else []
    
    def extract_part_numbers(self, brand_codes: list, brand_part_text: str) -> list:
        """
        从品牌编码及件号的完整文本中提取件号
        
        算法：
        1. 获取唯一的品牌编码列表
        2. 在完整文本中查找每个品牌编码的出现位置
        3. 提取品牌编码后面的内容作为件号（直到下一个品牌编码或行末）
        """
        import re
        results = []
        
        # 预处理品牌编码，去重并保持顺序
        unique_brand_codes = []
        seen = set()
        for bc in brand_codes:
            bc_upper = bc.strip().upper()
            if bc_upper and bc_upper not in seen:
                unique_brand_codes.append(bc_upper)
                seen.add(bc_upper)
        
        if not unique_brand_codes or not brand_part_text:
            return results
        
        # 统一处理空格/换行，保留原始大小写以便提取
        text = brand_part_text.strip()
        # 将换行符转为空格，便于处理
        text = ' '.join(text.split())
        
        self.log(f"  去重后的品牌编码: {unique_brand_codes}", "info")
        self.log(f"  品牌编码及件号原文: {text[:200]}...", "info")
        
        # 对于每个品牌编码，在文本中找到所有出现位置并提取件号
        for brand_code in unique_brand_codes:
            # 允许件号包含 字母(不分大小写)、数字、连字符、点、斜杠、下划线
            # 使用 re.IGNORECASE 匹配品牌名
            pattern = rf'\b{re.escape(brand_code)}\s*([a-zA-Z0-9][a-zA-Z0-9\-\.\/_]+)'
            matches = re.findall(pattern, text, re.IGNORECASE)
            
            for part_number in matches:
                # 统一转为大写输出
                part_number = part_number.strip().upper()
                # 验证件号不是品牌编码
                if part_number and part_number not in unique_brand_codes:
                    # 件号应该至少有4个字符（避免误判）
                    if len(part_number) >= 4:
                        results.append((brand_code, part_number))
        
        return results
    
    def process_image(self, image_path: Path) -> tuple:
        """
        处理单张图片的完整流程
        返回: (success, message, records_count)
        """
        try:
            # 打开图片
            image = Image.open(image_path)
            
            # OCR识别各区域
            ocr_results = {}
            brand_part_text = ''  # 品牌编码及件号的完整文本
            
            for region_name, region_coords in self.regions.items():
                cropped = self.crop_region(image, region_coords)
                base64_img = self.image_to_base64(cropped)
                
                if region_name == '品牌编码及件号':
                    # 获取完整文本
                    brand_part_text = self.call_ocr_api(base64_img, return_text=True)
                elif region_name == '品牌编码':
                    # 获取品牌编码列表
                    texts = self.call_ocr_api(base64_img, return_text=False)
                    ocr_results[region_name] = texts
                else:
                    # 其他区域
                    texts = self.call_ocr_api(base64_img, return_text=False)
                    ocr_results[region_name] = texts
            
            # 提取单值字段
            main_brand_code = ocr_results.get('主品牌编码', [''])[0] if ocr_results.get('主品牌编码') else ''
            convert_code = ocr_results.get('转换码', [''])[0] if ocr_results.get('转换码') else ''
            english_name = ocr_results.get('英文名称', [''])[0] if ocr_results.get('英文名称') else ''
            
            # TRIM和UPPER
            main_brand_code = main_brand_code.strip().upper()
            convert_code = convert_code.strip().upper()
            english_name = english_name.strip().upper()
            
            # 输出OCR识别结果日志
            self.log(f"--- OCR识别结果 [{image_path.name}] ---", "info")
            self.log(f"  主品牌编码: {main_brand_code}", "info")
            self.log(f"  转换码: {convert_code}", "info")
            self.log(f"  英文名称: {english_name}", "info")
            
            brand_codes = ocr_results.get('品牌编码', [])
            
            self.log(f"  品牌编码列 ({len(brand_codes)} 项):", "info")
            for i, bc in enumerate(brand_codes):
                self.log(f"    [{i}] {bc}", "info")
            
            # 提取件号
            part_number_pairs = self.extract_part_numbers(brand_codes, brand_part_text)
            
            self.log(f"  提取的件号对 ({len(part_number_pairs)} 对):", "info")
            for bc, pn in part_number_pairs:
                self.log(f"    品牌编码={bc}, 件号={pn}", "info")
            
            # 构建输出记录（单张图片内去重）
            new_records = []
            seen_in_image = set()  # 只在当前图片内去重
            source_file = image_path.name
            
            for brand_code, part_number in part_number_pairs:
                # 查找通用品牌
                universal_brand = self.brand_mapping.get(brand_code)
                if universal_brand is None:
                    self.log(f"  ⚠️ 警告: 品牌编码 [{brand_code}] 在 brandCode.csv 中未找到映射，已自动设为相同值", "warning")
                    universal_brand = brand_code
                
                # 收集所有可能的件号变体
                pns_to_add = [part_number]
                
                # 1. 前导零变体: 如果以0开头，增加一个移除所有前导0的版本
                if part_number.startswith('0'):
                    stripped_pn = part_number.lstrip('0')
                    if stripped_pn and stripped_pn != part_number:
                        pns_to_add.append(stripped_pn)
                
                # 2. 特殊字符变体: 移除 - . / _
                special_chars = "-./_"
                if any(char in part_number for char in special_chars):
                    cleaned_pn = part_number
                    for char in special_chars:
                        cleaned_pn = cleaned_pn.replace(char, '')
                    
                    if cleaned_pn and cleaned_pn != part_number:
                        pns_to_add.append(cleaned_pn)
                        # 如果清理后的号也以0开头，同样增加移除前导0的版本
                        if cleaned_pn.startswith('0'):
                            stripped_cleaned = cleaned_pn.lstrip('0')
                            if stripped_cleaned and stripped_cleaned != cleaned_pn:
                                pns_to_add.append(stripped_cleaned)
                
                # 遍历所有变体并添加
                for pn in pns_to_add:
                    record = (
                        main_brand_code,
                        convert_code,
                        english_name,
                        universal_brand,
                        brand_code,
                        pn
                    )
                    
                    # 在当前图片内去重并添加
                    if record not in seen_in_image:
                        new_records.append(record + (source_file,))
                        seen_in_image.add(record)
            
            # 保存到CSV
            if new_records:
                self.save_to_csv(new_records)
            
            # 移动图片到OK目录
            self.move_to_ok_folder(image_path)
            
            # 构建返回消息，包含新增记录的详情
            if new_records:
                record_details = ', '.join([f"{r[4]}:{r[5]}" for r in new_records])
                return True, f"处理成功，新增 {len(new_records)} 条记录: {record_details}", len(new_records)
            else:
                return True, "处理成功，无新记录", 0
            
        except Exception as e:
            return False, f"处理失败: {str(e)}", 0
    
    def save_to_csv(self, records: list):
        """保存结果到CSV"""
        file_exists = self.output_csv_path.exists()
        
        with open(self.output_csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # 如果文件不存在，写入表头
            if not file_exists:
                writer.writerow(['主品牌编码', '转换码', '英文名称', '通用品牌', '品牌编码', '通用编码', '来源文件'])
            
            writer.writerows(records)
    
    def move_to_ok_folder(self, image_path: Path):
        """移动处理完的图片到OK目录"""
        dest_path = self.output_dir / image_path.name
        
        # 如果目标文件已存在，添加时间戳
        if dest_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            stem = image_path.stem
            suffix = image_path.suffix
            dest_path = self.output_dir / f"{stem}_{timestamp}{suffix}"
        
        shutil.move(str(image_path), str(dest_path))
    
    def get_pending_images(self) -> list:
        """获取待处理的图片列表"""
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff'}
        images = []
        
        if self.input_dir.exists():
            for file_path in self.input_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                    images.append(file_path)
        
        return sorted(images)


class ProcessorThread(QThread):
    """图片处理线程"""
    progress = Signal(str, str)  # message, level (info/success/error)
    finished_all = Signal(int)  # total_records
    
    def __init__(self, processor: OCRProcessor, images: list):
        super().__init__()
        self.processor = processor
        self.images = images
        self._running = True
    
    def run(self):
        total_records = 0
        
        for image_path in self.images:
            if not self._running:
                break
            
            self.progress.emit(f"正在处理: {image_path.name}", "info")
            success, message, count = self.processor.process_image(image_path)
            
            if success:
                self.progress.emit(f"✓ {image_path.name}: {message}", "success")
                total_records += count
            else:
                self.progress.emit(f"✗ {image_path.name}: {message}", "error")
        
        self.finished_all.emit(total_records)
    
    def stop(self):
        self._running = False


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR 图片处理程序")
        self.setMinimumSize(700, 500)
        
        # 获取程序所在目录
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe
            self.base_dir = Path(sys.executable).parent
        else:
            # 如果是源码运行
            self.base_dir = Path(__file__).parent
        
        # 初始化处理器
        self.processor = OCRProcessor(str(self.base_dir))
        
        # 处理线程
        self.process_thread = None
        
        # 文件监控
        self.watcher = QFileSystemWatcher()
        self.watcher.directoryChanged.connect(self.on_directory_changed)
        self.watching = False
        
        # 延迟处理定时器（避免文件未写入完成就处理）
        self.process_timer = QTimer()
        self.process_timer.setSingleShot(True)
        self.process_timer.timeout.connect(self.process_pending_images)
        
        self.setup_ui()
        self.update_status()
    
    def setup_ui(self):
        """设置界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # 目录信息组
        dir_group = QGroupBox("目录配置")
        dir_layout = QVBoxLayout(dir_group)
        
        self.input_dir_label = QLabel(f"监测目录: {self.processor.input_dir}")
        self.output_dir_label = QLabel(f"输出目录: {self.processor.output_dir}")
        self.csv_label = QLabel(f"CSV文件: {self.processor.output_csv_path}")
        
        dir_layout.addWidget(self.input_dir_label)
        dir_layout.addWidget(self.output_dir_label)
        dir_layout.addWidget(self.csv_label)
        layout.addWidget(dir_group)
        
        # 状态组
        status_group = QGroupBox("状态")
        status_layout = QHBoxLayout(status_group)
        
        self.pending_label = QLabel("待处理: 0 张")
        self.pending_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        
        self.watching_label = QLabel("监测状态: 未启动")
        self.watching_label.setStyleSheet("font-weight: bold;")
        
        status_layout.addWidget(self.pending_label)
        status_layout.addStretch()
        status_layout.addWidget(self.watching_label)
        layout.addWidget(status_group)
        
        # 按钮组
        btn_layout = QHBoxLayout()
        
        self.process_btn = QPushButton("处理当前图片")
        self.process_btn.setMinimumHeight(40)
        self.process_btn.clicked.connect(self.process_pending_images)
        
        self.watch_btn = QPushButton("开始监测")
        self.watch_btn.setMinimumHeight(40)
        self.watch_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.watch_btn.clicked.connect(self.toggle_watching)
        
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.setMinimumHeight(40)
        self.refresh_btn.clicked.connect(self.update_status)
        
        btn_layout.addWidget(self.process_btn)
        btn_layout.addWidget(self.watch_btn)
        btn_layout.addWidget(self.refresh_btn)
        layout.addLayout(btn_layout)
        
        # 日志区域
        log_group = QGroupBox("处理日志")
        log_layout = QVBoxLayout(log_group)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)
        
        # 日志底部按钮区域
        log_btn_layout = QHBoxLayout()
        
        self.stop_btn = QPushButton("停止处理")
        self.stop_btn.clicked.connect(self.stop_all)
        self.stop_btn.setStyleSheet("background-color: #F44336; color: white; font-weight: bold;")
        
        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.clicked.connect(self.log_text.clear)
        
        dedup_btn = QPushButton("一键去重")
        dedup_btn.clicked.connect(self.deduplicate_csv)
        
        log_btn_layout.addWidget(self.stop_btn)
        log_btn_layout.addWidget(clear_log_btn)
        log_btn_layout.addWidget(dedup_btn)
        log_layout.addLayout(log_btn_layout)
        
        layout.addWidget(log_group)
        
        # 样式
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
            QPushButton:disabled {
                background-color: #BDBDBD;
            }
        """)
    
    def log(self, message: str, level: str = "info"):
        """添加日志到GUI"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # 同时输出到控制台
        print(f"[{timestamp}] [{level.upper()}] {message}")
        
        color_map = {
            "info": "#333333",
            "success": "#4CAF50",
            "error": "#F44336",
            "warning": "#FF9800"
        }
        color = color_map.get(level, "#333333")
        
        html = f'<span style="color: #999;">[{timestamp}]</span> <span style="color: {color};">{message}</span>'
        self.log_text.append(html)
        
        # 滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def deduplicate_csv(self):
        """一键去重CSV文件"""
        csv_path = self.processor.output_csv_path
        
        if not csv_path.exists():
            self.log("CSV文件不存在，无需去重", "warning")
            return
        
        try:
            # 读取所有记录
            rows = []
            with open(csv_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header:
                    rows.append(header)
                for row in reader:
                    rows.append(row)
            
            if len(rows) <= 1:
                self.log("CSV文件为空，无需去重", "info")
                return
            
            # 去重（基于前6列：主品牌编码,转换码,英文名称,通用品牌,品牌编码,通用编码）
            seen = set()
            unique_rows = [rows[0]]  # 保留表头
            
            for row in rows[1:]:
                if len(row) >= 6:
                    key = tuple(row[:6])
                    if key not in seen:
                        seen.add(key)
                        unique_rows.append(row)
            
            removed_count = len(rows) - len(unique_rows)
            
            if removed_count == 0:
                self.log("没有发现重复记录", "info")
                return
            
            # 重写CSV文件
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(unique_rows)
            
            self.log(f"去重完成，删除了 {removed_count} 条重复记录，保留 {len(unique_rows) - 1} 条记录", "success")
            
        except Exception as e:
            self.log(f"去重失败: {str(e)}", "error")
    
    def update_status(self):
        """更新状态显示"""
        images = self.processor.get_pending_images()
        self.pending_label.setText(f"待处理: {len(images)} 张")
        
        if self.watching:
            self.watching_label.setText("监测状态: 运行中")
            self.watching_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        else:
            self.watching_label.setText("监测状态: 未启动")
            self.watching_label.setStyleSheet("font-weight: bold; color: #999;")
    
    def toggle_watching(self):
        """切换监测状态"""
        if self.watching:
            self.stop_watching()
        else:
            self.start_watching()
    
    def start_watching(self):
        """开始监测目录"""
        input_dir = str(self.processor.input_dir)
        
        if not os.path.exists(input_dir):
            QMessageBox.warning(self, "警告", f"监测目录不存在: {input_dir}")
            return
        
        self.watcher.addPath(input_dir)
        self.watching = True
        self.watch_btn.setText("停止监测")
        self.watch_btn.setStyleSheet("background-color: #F44336; color: white; font-weight: bold;")
        self.log("✅ 开始监测目录变化", "success")
        self.update_status()
        
        # 立即处理现有图片
        self.process_pending_images()
    
    def stop_watching(self):
        """停止监测目录"""
        self.watcher.removePath(str(self.processor.input_dir))
        self.watching = False
        self.watch_btn.setText("开始监测")
        self.watch_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.log("⛔ 停止监测目录变化", "warning")
        self.update_status()

    def stop_all(self):
        """停止监测和停止处理"""
        # 1. 停止监测
        if self.watching:
            self.stop_watching()
        
        # 2. 停止待处理定时器
        if self.process_timer.isActive():
            self.process_timer.stop()
            self.log("已取消待处理的定时任务", "info")
            
        # 3. 停止处理任务
        if self.process_thread and self.process_thread.isRunning():
            self.process_thread.stop()
            self.log("🛑 正在停止当前处理任务...", "warning")
        else:
            self.log("目前没有正在运行的处理任务", "info")
    
    def on_directory_changed(self, path: str):
        """目录变化回调"""
        self.log(f"检测到目录变化: {path}", "info")
        # 延迟1秒处理，确保文件写入完成
        self.process_timer.start(1000)
    
    def process_pending_images(self):
        """处理待处理的图片"""
        if self.process_thread and self.process_thread.isRunning():
            self.log("已有处理任务在运行中", "warning")
            return
        
        images = self.processor.get_pending_images()
        
        if not images:
            self.log("没有待处理的图片", "info")
            self.update_status()
            return
        
        self.log(f"开始处理 {len(images)} 张图片...", "info")
        
        # 禁用按钮
        self.process_btn.setEnabled(False)
        
        # 启动处理线程
        self.process_thread = ProcessorThread(self.processor, images)
        self.process_thread.progress.connect(self.log)
        self.process_thread.finished_all.connect(self.on_process_finished)
        self.process_thread.start()
    
    def on_process_finished(self, total_records: int):
        """处理完成回调"""
        self.log(f"处理完成，共新增 {total_records} 条记录", "success")
        self.process_btn.setEnabled(True)
        self.update_status()
    
    def closeEvent(self, event):
        """关闭窗口事件"""
        if self.process_thread and self.process_thread.isRunning():
            self.process_thread.stop()
            self.process_thread.wait()
        
        if self.watching:
            self.stop_watching()
        
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
