import re

def get_iso6346_value(char):
    """
    Returns the numerical value of a character according to ISO 6346.
    A=10, B=12, C=13... Z=38 (excluding 11, 22, 33)
    """
    if char.isdigit():
        return int(char)
    
    # Calculate base value: A=10, B=11, C=12...
    val = ord(char.upper()) - ord('A') + 10
    
    # Adjust for skipping multiples of 11
    if val >= 11:
        val += 1
    if val >= 22:
        val += 1
    if val >= 33:
        val += 1
    return val

def calculate_check_digit(prefix, serial):
    """
    Calculates the ISO 6346 check digit for a 4-letter prefix and 6-digit serial.
    """
    if len(prefix) != 4 or len(serial) != 6:
        raise ValueError("Prefix must be 4 letters and serial must be 6 digits.")
    
    full_str = prefix.upper() + serial
    total = 0
    for i in range(10):
        val = get_iso6346_value(full_str[i])
        total += val * (2**i)
    
    # Check digit is sum mod 11. If it's 10, it becomes 0.
    rem = total % 11
    return rem % 10

def complete_container_number(digits_7):
    """
    Completes a 7-digit input (6-digit serial + 1-digit check digit) with a prefix.
    Logic:
    - If serial < 050000, try TBCU
    - Otherwise try TBJU
    """
    if not re.match(r'^\d{7}$', digits_7):
        return None, "Invalid input: Must be exactly 7 digits."
    
    serial = digits_7[:6]
    provided_check = int(digits_7[6])
    serial_int = int(serial)
    
    # Prefix guessing logic
    if serial_int < 50000:
        prefix = "TBCU"
    else:
        prefix = "TBJU"
    
    calc_check = calculate_check_digit(prefix, serial)
    
    full_number = f"{prefix}{serial}{provided_check}"
    
    if calc_check == provided_check:
        return full_number, f"Success: Matches {prefix} (Calculated check digit: {calc_check})"
    else:
        # Fallback: try the other prefix just in case
        other_prefix = "TBJU" if prefix == "TBCU" else "TBCU"
        other_calc_check = calculate_check_digit(other_prefix, serial)
        
        if other_calc_check == provided_check:
            return f"{other_prefix}{serial}{provided_check}", f"Warning: Range check failed but matches {other_prefix} (Calculated: {other_calc_check})"
        else:
            return None, f"Error: Check digit mismatch. (For {prefix} expected {calc_check}, for {other_prefix} expected {other_calc_check}; provided {provided_check})"

# Example usage and tests
if __name__ == "__main__":
    # 测试案例
    test_cases = [
        "0000016",  # TBCU 范围 (000001) -> 预期 TBCU0000016
        "5000009",  # TBJU 范围 (500000) -> 预期 TBJU5000009
        "0499997",  # TBCU 边界
        "0500004",  # TBJU 边界
    ]
    
    print(f"{'输入(7位)':<10} | {'补全后箱号':<15} | {'状态/信息'}")
    print("-" * 70)
    for tc in test_cases:
        result, msg = complete_container_number(tc)
        print(f"{tc:<10} | {str(result):<15} | {msg}")
