#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/24
# name: Haowen Cui

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import os
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from summarization.transcript_splitter import split_transcript

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
SUMMARY_DIR = BASE_DIR / "data" / "summaries"

load_dotenv(BASE_DIR / ".env")

PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
            (
                "system",
                """
                你是一名视频内容总结助手。
                
                请根据用户提供的视频文字稿，生成准确、清晰的中文总结。
                
                要求：
                1. summary 使用一段话概括视频主要内容。
                2. key_points 包含 3 到 5 个关键内容。
                3. keywords 包含 5 到 10 个关键词。
                4. 不要添加文字稿中没有提到的信息。
                5. 字幕可能存在识别错误，请结合上下文理解。
                6. 不要因为字幕识别错误而编造人物、平台或事件名称。
                """,
            ),
            ("human",
                """
                {retry_instruction}
                
                视频文字稿：
                
                {transcript}
                """,
            ),
        ]
)


CHUNK_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
            你是一名视频内容总结助手。
            
            请根据当前视频分段的文字稿生成结构化中文总结。
            
            要求：
            1. summary 用一段话概括当前分段的主要内容。
            2. key_points 提取 2 到 4 个关键内容。
            3. keywords 提取 3 到 6 个关键词。
            4. 只总结当前分段，不要推测其他分段内容。
            5. 不要添加文字稿中没有提到的信息。
            6. 字幕可能存在识别错误，请结合上下文理解，但不要编造事实。
            """,
        ),
        (
            "human",
            """
            {retry_instruction}
            
            分段编号：{chunk_index}
            开始时间：{start}
            结束时间：{end}
            
            分段文字稿：
            
            {text}
            """,
        ),
    ]
)

MERGE_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
            你是一名视频内容总结助手。

            请将按视频时间顺序排列的多个局部总结，合并成一份准确、
            连贯的全局结构化总结。

            要求：
            1. summary 使用一段话概括整段视频的核心内容和发展脉络。
            2. key_points 包含 3 到 5 个全局关键内容。
            3. keywords 包含 5 到 10 个关键词。
            4. 合并重复信息，不要按分段逐条复述。
            5. 根据完整上下文重新判断主次，并保留事件之间的因果和时间关系。
            6. 只能使用局部总结中出现的信息，不要补充外部知识或编造事实。
            7. 同一实体在不同分段中的写法不一致时，优先使用信息更完整、
               在多个分段中更一致的写法；无法确认时保留原写法。
            """,
        ),
        (
            "human",
            """
            {retry_instruction}

            按时间顺序排列的局部总结：

            {chunk_summaries}
            """,
        ),
    ]
)


class VideoSummary(BaseModel):
    summary: str = Field(
        description="用一段中文概括视频的主要内容"
    )

    key_points: list[str] = Field(
        description="3到5个关键内容"
    )

    keywords: list[str] = Field(
        description="5到10个关键词"
    )

class ChunkSummary(BaseModel):
    """
    单个文字稿分段的总结结构。
    """

    summary: str = Field(
        description="用一段中文概括当前分段的主要内容"
    )

    key_points: list[str] = Field(
        min_length=2,
        max_length=4,
        description="当前分段的2到4个关键内容",
    )

    keywords: list[str] = Field(
        min_length=3,
        max_length=6,
        description="当前分段的3到6个关键词",
    )

def load_config():
    """
    读取模型配置。

    :return: 模型配置信息
    """
    with open(CONFIG_PATH,"r",encoding="utf-8", ) as file:
        config = json.load(file)

    return config

def create_model():
    """
    创建百炼 Qwen 模型。

    :return: LangChain 模型
    """
    config = load_config()

    api_key=os.environ.get("SUMMARY_VIDEO_API_KEY")
    model_name = config.get("model")
    base_url = config.get("base_url")

    if not api_key:
        raise ValueError("环境变量 DASHSCOPE_API_KEY 未设置，请在项目根目录的 .env 中配置")

    if not model_name:
        raise ValueError("config.json 中没有 model")

    if not base_url:
        raise ValueError("config.json 中没有 base_url")

    model = ChatOpenAI(
        api_key=api_key,
        model=model_name,
        base_url=base_url,
        temperature=0.2,
    )

    return model

def load_transcript(pathname):
    """
    读取 transcript JSON。

    :param pathname: transcript JSON 文件路径
    :return: 文字稿数据
    """
    with open(pathname,"r",encoding="utf-8",) as file:
        transcript = json.load(file)

    if not transcript.get("video_id"):
        raise ValueError("文字稿中没有 video_id")

    if not transcript.get("text"):
        raise ValueError("文字稿中没有 text")

    return transcript

def get_raw_response_text(raw_message):
    """
    从模型原始回复中提取可读内容。

    :param raw_message: LangChain 原始模型回复
    :return: 原始回复内容
    """
    if raw_message is None:
        return ""

    if raw_message.content:
        return str(raw_message.content)

    return str(raw_message.additional_kwargs)

def summarize_video(transcript_text):
    """
    根据视频文字稿生成结构化总结。

    第一次生成失败后，最多重新尝试生成一次。

    :param transcript_text: 完整视频文字稿
    :return: 视频总结字典
    """
    model = create_model()

    structured_model = (
        model.with_structured_output(
            VideoSummary,
            include_raw=True,
        )
    )

    chain = (PROMPT_TEMPLATE| structured_model)

    retry_instruction = ""
    last_error = None

    for attempt in range(2):
        try:
            result = chain.invoke(
                {
                    "transcript": transcript_text,
                    "retry_instruction": (retry_instruction),
                }
            )

            summary = result.get("parsed")
            parsing_error = result.get("parsing_error")

            if summary is not None:
                return summary.model_dump()

            last_error = parsing_error

            raw_text = get_raw_response_text(result.get("raw"))

            retry_instruction = f"""
            上一次输出没有通过结构验证，请重新生成。
            
            错误信息：
            {parsing_error}
            
            上一次模型输出：
            {raw_text}
            
            请严格按照规定的字段和数量重新生成。
            不要解释错误，只返回符合结构的结果。
            """

        except Exception as error:
            last_error = error

            retry_instruction = f"""
            上一次模型调用或结构化解析失败。
            
            错误信息：
            {error}
            
            请重新阅读视频文字稿，并严格按照规定的结构生成结果。
            """

        if attempt < 1:
            print(
                f"结构化总结失败，"
                f"正在进行第 {attempt + 1} 次重试"
            )

    raise RuntimeError(
        "首次生成及一次重试均失败："
        f"{last_error}"
    )

def save_summary(summary, video_id):
    """
    保存视频总结。

    :param summary: 视频总结字典
    :param video_id: Bilibili 视频 ID
    :return: 总结文件路径
    """
    SUMMARY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary["video_id"] = video_id

    output_path = SUMMARY_DIR / f"{video_id}.json"

    output_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2,),encoding="utf-8",)

    return output_path

def create_chunk_summary_chain():
    """
    创建分段总结链。

    :return: LangChain 分段总结链
    """
    model = create_model()

    structured_model = model.with_structured_output(
        ChunkSummary,
        include_raw=True,
    )

    chain = (CHUNK_PROMPT_TEMPLATE| structured_model)

    return chain

def summarize_chunk(chain, chunk):
    """
    总结单个文字稿分段。

    首次失败后最多重试两次。

    :param chain: LangChain 分段总结链
    :param chunk: 文字稿分段
    :return: 分段总结
    """
    retry_instruction = ""
    last_error = None

    for attempt in range(3):
        result = chain.invoke(
            {
                "chunk_index": chunk["chunk_index"],
                "start": chunk["start"],
                "end": chunk["end"],
                "text": chunk["text"],
                "retry_instruction": retry_instruction,
            }
        )

        chunk_summary = result.get("parsed")
        parsing_error = result.get(
            "parsing_error"
        )

        if chunk_summary is not None:
            summary_data = chunk_summary.model_dump()

            summary_data["chunk_index"] = chunk["chunk_index"]
            summary_data["start"] = chunk["start"]
            summary_data["end"] = chunk["end"]

            return summary_data

        last_error = parsing_error

        retry_instruction = f"""
        上一次输出没有通过结构验证。
        
        错误信息：
        {parsing_error}
        
        请重新生成，并严格遵守规定的输出结构。
        """

        if attempt < 2:
            print(
                f"第 {chunk['chunk_index']} 段总结失败，"
                f"正在进行第 {attempt + 1} 次重试"
            )

    raise RuntimeError(
        f"第 {chunk['chunk_index']} 段连续重试后仍然失败："
        f"{last_error}"
    )

def summarize_chunks(chunks, max_workers=4):
    """
    并发总结所有文字稿分段。

    :param chunks: 文字稿分段列表
    :param max_workers: 最大并发数
    :return: 所有分段的局部总结
    """
    if not chunks:
        return []

    if max_workers < 1:
        raise ValueError("max_workers 必须大于等于 1")

    chain = create_chunk_summary_chain()
    chunk_summaries = []
    worker_count = min(max_workers, len(chunks))

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_chunk = {
            executor.submit(summarize_chunk, chain, chunk): chunk
            for chunk in chunks
        }

        for future in as_completed(future_to_chunk):
            chunk = future_to_chunk[future]
            chunk_summary = future.result()
            chunk_summaries.append(chunk_summary)
            print(
                f"已完成第 {chunk['chunk_index']}/{len(chunks)} 段总结"
            )

    return sorted(
        chunk_summaries,
        key=lambda item: item["chunk_index"],
    )

def merge_chunk_summaries(chunk_summaries):
    """
    将多个局部总结合并为一份全局视频总结。

    首次失败后最多重新尝试生成一次。

    :param chunk_summaries: 局部总结列表
    :return: 全局视频总结字典
    """
    if not chunk_summaries:
        raise ValueError("没有可合并的局部总结")

    ordered_summaries = sorted(
        chunk_summaries,
        key=lambda item: item["chunk_index"],
    )
    summaries_text = json.dumps(
        ordered_summaries,
        ensure_ascii=False,
        indent=2,
    )

    model = create_model()
    structured_model = model.with_structured_output(
        VideoSummary,
        include_raw=True,
    )
    chain = (MERGE_PROMPT_TEMPLATE | structured_model)

    retry_instruction = ""
    last_error = None

    for attempt in range(2):
        try:
            result = chain.invoke(
                {
                    "chunk_summaries": summaries_text,
                    "retry_instruction": retry_instruction,
                }
            )

            summary = result.get("parsed")
            parsing_error = result.get("parsing_error")

            if summary is not None:
                return summary.model_dump()

            last_error = parsing_error
            raw_text = get_raw_response_text(result.get("raw"))
            retry_instruction = f"""
            上一次输出没有通过结构验证，请重新合并。

            错误信息：
            {parsing_error}

            上一次模型输出：
            {raw_text}

            请严格按照规定的字段和数量返回全局总结。
            """
        except Exception as error:
            last_error = error
            retry_instruction = f"""
            上一次模型调用或结构化解析失败。

            错误信息：
            {error}

            请重新合并局部总结，并严格按照规定的结构返回结果。
            """

        if attempt < 1:
            print("合并局部总结失败，正在进行第 1 次重试")

    raise RuntimeError(
        "首次合并及一次重试均失败："
        f"{last_error}"
    )



def _test():
    """
    测试百炼 Qwen 是否可以正常调用。

    :return: None
    """
    start_time = time.perf_counter()

    model = create_model()

    response = model.invoke("请只回复：Qwen 模型连接成功" )

    end_time = time.perf_counter()

    print("-" * 40)
    print(response.content)
    print(
        f"模型调用耗时："
        f"{end_time - start_time:.2f} 秒"
    )
    print("-" * 40)

def _vallidation_sig():
    try:
        start_time=time.perf_counter()

        transcript_path=(
                BASE_DIR
                / "data"
                / "transcripts"
                / "BV1Ru6BBwEAn.json"
        )

        transcript=load_transcript(transcript_path)

        summary=summarize_video(transcript["text"])

        summary_path=save_summary(summary,transcript["video_id"],)

        end_time=time.perf_counter()

        print("-" * 40)
        print(json.dumps(summary,ensure_ascii=False,indent=2,))
        print("-" * 40)
        print(f"总结保存路径：{summary_path}")
        print(f"视频总结总耗时：{end_time - start_time:.2f} 秒")

    except Exception as error:
        print(f"视频总结失败：{error}")


if __name__ == "__main__":
    transcript_path=(
            BASE_DIR
            / "data"
            / "transcripts"
            / "BV1Ru6BBwEAn.json"
    )

    transcript=load_transcript(transcript_path)
    chunks=split_transcript(transcript["segments"],max_characters=3000,)

    print(f"共分成 {len(chunks)} 段")

    for chunk in chunks:
        print("-" * 40)
        print(f"分段编号：{chunk['chunk_index']}")
        print(f"时间范围：{chunk['start']} - {chunk['end']}")
        print(f"字幕数量：{chunk['segment_count']}")
        print(f"字符数量：{len(chunk['text'])}")

    chunk_summaries=summarize_chunks(chunks)
    summary=merge_chunk_summaries(chunk_summaries)
    summary_path=save_summary(summary,transcript["video_id"])

    print("-" * 40)
    print(json.dumps(summary,ensure_ascii=False,indent=2,))
    print("-" * 40)
    print(f"总结保存路径：{summary_path}")
