from transformers import pipeline

class AIHelper:
    def __init__(self):
        self.nlp = pipeline(
            "question-answering",
            model="deepset/roberta-base-squad2"
        )
    
    def analyze(self, text):
        """Phân tích văn bản để tìm câu hỏi và trả lời"""
        if "?" not in text:
            return None
        
        try:
            # Làm sạch text (ví dụ)
            clean_text = text.replace("\n", " ").strip()
            
            # Tách giả định câu hỏi và ngữ cảnh
            # (Có thể tùy chỉnh theo format câu hỏi cụ thể)
            question_end = clean_text.index("?")
            question = clean_text[:question_end+1]
            context = clean_text[question_end+1:]
            
            if not question or not context:
                return None
            
            result = self.nlp(question=question, context=context)
            return result["answer"]
        except Exception:
            return None