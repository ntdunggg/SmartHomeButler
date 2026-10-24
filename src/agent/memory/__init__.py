"""Layer 3 (memory) — Event-centric long-term memory (spec §19-26).

EMem event store + HiGMem hierarchical Event→Turn retrieval. Memory là BẰNG CHỨNG
về *chuyện đã xảy ra*, KHÔNG override live state (spec §73 invariant 3).
"""
