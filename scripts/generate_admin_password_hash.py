"""Generate a password hash compatible with the hospital admin backend."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import secrets
from getpass import getpass
from pathlib import Path


PASSWORD_ITERATIONS = 310_000


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def generate_hash() -> str:
    password = getpass("请输入新的医院管理员密码（输入不会显示）: ")
    confirmation = getpass("请再次输入新密码: ")
    if password != confirmation:
        raise SystemExit("两次输入的密码不一致")
    if not 8 <= len(password) <= 128:
        raise SystemExit("密码长度必须为 8-128 位")

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        PASSWORD_ITERATIONS,
    )
    encoded = "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )
    return encoded


def write_reset_sql(encoded: str, phone: str, output: Path) -> None:
    escaped_phone = phone.replace("'", "''")
    sql = f"""START TRANSACTION;
UPDATE `user_info`
SET `password` = '{encoded}'
WHERE `phone` = '{escaped_phone}' AND `role` = '管理员';
DELETE FROM `user_session`
WHERE `userID` = (
  SELECT `userID` FROM `user_info`
  WHERE `phone` = '{escaped_phone}' LIMIT 1
);
COMMIT;
SELECT CHAR_LENGTH(`password`) AS `stored_length`
FROM `user_info`
WHERE `phone` = '{escaped_phone}';
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(sql, encoding="utf-8", newline="\n")


def verify_hash() -> None:
    encoded = input("请粘贴数据库中的整行密码哈希: ").strip()
    password = getpass("请输入准备在登录页使用的密码（输入不会显示）: ")
    if verify_password(password, encoded):
        print("校验结果：匹配")
    else:
        print("校验结果：不匹配")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="verify a password against a generated hash")
    parser.add_argument("--sql-output", type=Path, help="write a complete password reset SQL file")
    parser.add_argument("--phone", default="13800000000", help="hospital administrator phone number")
    args = parser.parse_args()
    if args.verify:
        verify_hash()
    else:
        encoded = generate_hash()
        if args.sql_output:
            write_reset_sql(encoded, args.phone, args.sql_output)
            print(f"已生成密码重置 SQL：{args.sql_output.resolve()}")
        else:
            print("\n复制下面这一整行哈希值（它不是密码）：")
            print(encoded)


if __name__ == "__main__":
    main()
