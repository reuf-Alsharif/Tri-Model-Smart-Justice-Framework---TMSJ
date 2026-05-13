import json
from collections import Counter

# قراءة الملف
with open('cases_6039174d_5200.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=" * 50)
print("تحليل ملف القضايا")
print("=" * 50)

# 1. عدد القضايا الإجمالي
print(f"\n1. إجمالي عدد القضايا: {len(data)}")

# 2. استخراج أرقام الأحكام
judgment_numbers = [c.get('judgmentNumber') for c in data if c.get('judgmentNumber')]
print(f"2. عدد القضايا التي لها رقم حكم: {len(judgment_numbers)}")

# 3. التحقق من التكرار
counts = Counter(judgment_numbers)
unique_count = len(counts)
print(f"3. عدد أرقام الأحكام الفريدة: {unique_count}")

# 4. الأرقام المكررة
dups = {n: c for n, c in counts.items() if c > 1}
print(f"4. عدد الأرقام المكررة: {len(dups)}")

if dups:
    print("\n   أكثر 15 رقم تكراراً:")
    sorted_dups = sorted(dups.items(), key=lambda x: x[1], reverse=True)[:15]
    for i, (num, count) in enumerate(sorted_dups, 1):
        print(f"   {i}. رقم الحكم {num}: مكرر {count} مرات")

# 5. التحقق من تكرار المعرفات (id)
ids = [c.get('id') for c in data if c.get('id')]
id_counts = Counter(ids)
duplicate_ids = {id_: count for id_, count in id_counts.items() if count > 1}
print(f"\n5. عدد المعرفات (id) المكررة: {len(duplicate_ids)}")

# 6. إحصائيات المحاكم
courts = [c.get('court') for c in data if c.get('court')]
court_counts = Counter(courts)
print("\n6. توزيع القضايا حسب المحكمة:")
for court, count in court_counts.most_common():
    print(f"   - {court}: {count} قضية")

# 7. إحصائيات المدن
cities = [c.get('city') for c in data if c.get('city')]
city_counts = Counter(cities)
print("\n7. توزيع القضايا حسب المدينة:")
for city, count in city_counts.most_common():
    print(f"   - {city}: {count} قضية")

print("\n" + "=" * 50)
