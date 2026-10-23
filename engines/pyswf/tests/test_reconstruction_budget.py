from py_swf.decompile.native import reconstruct
from py_swf.decompile.lift import Lifter
from py_swf.decompile.ir import Instruction,ReconstructionError
import pytest


def test_instruction_budget_prevents_long_corpus_worker_timeout():
    instructions=[Instruction(i,i+1,"nop",()) for i in range(50001)]
    with pytest.raises(ReconstructionError,match="instruction budget"):
        reconstruct(instructions,Lifter("as2"))
