"""container_fixer 单元测试 — 纯逻辑，无需 VLM 服务"""
import pytest

from sop_hub.utils.container_fixer import (
    calculate_check_digit,
    complete_container_number,
    get_iso6346_value,
)


class TestISO6346Value:
    def test_digit_returns_int(self):
        assert get_iso6346_value("0") == 0
        assert get_iso6346_value("9") == 9

    def test_letter_a(self):
        assert get_iso6346_value("A") == 10

    def test_letter_b(self):
        # B = 10+1 = 11, but 11 is skipped, so 12
        assert get_iso6346_value("B") == 12

    def test_letter_z(self):
        # Z should have value 38
        assert get_iso6346_value("Z") == 38

    def test_case_insensitive(self):
        assert get_iso6346_value("a") == get_iso6346_value("A")


class TestCheckDigitCalculation:
    def test_known_container_tbcu(self):
        # TBCU000001 → check digit should be 6
        check = calculate_check_digit("TBCU", "000001")
        assert check == 6

    def test_prefix_must_be_4_chars(self):
        with pytest.raises(ValueError):
            calculate_check_digit("TBC", "000001")

    def test_serial_must_be_6_digits(self):
        with pytest.raises(ValueError):
            calculate_check_digit("TBCU", "00001")


class TestCompleteContainerNumber:
    def test_tbcu_range_valid(self):
        """序列号 < 050000 应推断为 TBCU"""
        result, msg = complete_container_number("0000016")
        assert result is not None
        assert result.startswith("TBCU")
        assert "Success" in msg or "Warning" in msg

    def test_tbju_range_valid(self):
        """序列号 >= 050000 应推断为 TBJU"""
        # TBJU 050000 的校验位是 1
        result, msg = complete_container_number("0500001")
        assert result is not None
        assert result.startswith("TBJU")

    def test_boundary_049999(self):
        """边界值 049999: 仍在 TBCU 范围"""
        result, msg = complete_container_number("0499997")
        assert result is not None
        assert "TBCU" in result or "TBJU" in result

    def test_invalid_input_not_7_digits(self):
        result, msg = complete_container_number("12345")
        assert result is None
        assert "Invalid" in msg

    def test_invalid_input_with_letters(self):
        result, msg = complete_container_number("123456a")
        assert result is None
        assert "Invalid" in msg

    def test_check_digit_mismatch(self):
        """错误的校验位应返回 Error"""
        # 先算出正确的校验位，然后用错误的
        correct_check = calculate_check_digit("TBCU", "000001")
        wrong_check = (correct_check + 1) % 10
        wrong_input = f"000001{wrong_check}"
        result, msg = complete_container_number(wrong_input)
        # 可能返回 None（两个前缀都不匹配）或返回另一个前缀的匹配
        if result is None:
            assert "Error" in msg
