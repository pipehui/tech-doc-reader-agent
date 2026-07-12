from langchain_text_splitters import RecursiveCharacterTextSplitter


def recursive_character_splitting(
    text: str,
    chunk_size: int = 300,
    chunk_overlap: int = 20,
) -> list[str]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", " ", ""],
    )
    return text_splitter.split_text(text)
