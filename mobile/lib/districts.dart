// The full NER-SHIELD service area: all eight North Eastern Region states. Kept as a static,
// embedded constant (not fetched from GET /api/v1/districts, unlike the web dashboard) so a
// citizen filling out a report during a connectivity outage — the exact scenario this app is
// built for — isn't blocked by a failed districts-list fetch just to pick their own district.
// Mirrors backend/app/main.py's NER_DISTRICTS; keep the two in sync if either changes.
//
// District boundaries and counts change periodically as new districts are notified (especially
// in Assam and Manipur); this reflects publicly available data as of this project's development
// and should be revalidated against the latest state gazette before real deployment.
const Map<String, List<String>> nerDistricts = {
  'Arunachal Pradesh': [
    'Tawang', 'West Kameng', 'East Kameng', 'Pakke-Kessang', 'Papum Pare', 'Kra Daadi',
    'Kurung Kumey', 'Kamle', 'Lower Subansiri', 'Upper Subansiri', 'West Siang', 'Lepa Rada',
    'East Siang', 'Siang', 'Upper Siang', 'Lower Siang', 'Lower Dibang Valley', 'Dibang Valley',
    'Anjaw', 'Lohit', 'Namsai', 'Changlang', 'Tirap', 'Longding', 'Shi Yomi',
    'Itanagar Capital Complex',
  ],
  'Assam': [
    'Baksa', 'Barpeta', 'Biswanath', 'Bongaigaon', 'Cachar', 'Charaideo', 'Chirang', 'Darrang',
    'Dhemaji', 'Dhubri', 'Dibrugarh', 'Dima Hasao', 'Goalpara', 'Golaghat', 'Hailakandi',
    'Hojai', 'Jorhat', 'Kamrup', 'Kamrup Metropolitan', 'Karbi Anglong', 'Karimganj',
    'Kokrajhar', 'Lakhimpur', 'Majuli', 'Morigaon', 'Nagaon', 'Nalbari', 'Sivasagar',
    'South Salmara-Mankachar', 'Sonitpur', 'Tinsukia', 'Udalguri', 'West Karbi Anglong',
    'Bajali', 'Tamulpur',
  ],
  'Manipur': [
    'Bishnupur', 'Chandel', 'Churachandpur', 'Imphal East', 'Imphal West', 'Jiribam',
    'Kakching', 'Kamjong', 'Kangpokpi', 'Noney', 'Pherzawl', 'Senapati', 'Tamenglong',
    'Tengnoupal', 'Thoubal', 'Ukhrul',
  ],
  'Meghalaya': [
    'East Khasi Hills', 'West Khasi Hills', 'South West Khasi Hills', 'Eastern West Khasi Hills',
    'Ri Bhoi', 'East Jaintia Hills', 'West Jaintia Hills', 'East Garo Hills', 'West Garo Hills',
    'South Garo Hills', 'North Garo Hills', 'South West Garo Hills',
  ],
  'Mizoram': [
    'Aizawl', 'Lunglei', 'Champhai', 'Mamit', 'Kolasib', 'Serchhip', 'Lawngtlai', 'Saiha',
    'Khawzawl', 'Hnahthial', 'Saitual',
  ],
  'Nagaland': [
    'Kohima', 'Dimapur', 'Mokokchung', 'Tuensang', 'Wokha', 'Zunheboto', 'Phek', 'Mon',
    'Longleng', 'Kiphire', 'Peren', 'Noklak', 'Chumoukedima', 'Niuland', 'Shamator', 'Tseminyu',
  ],
  'Sikkim': [
    'East Sikkim', 'West Sikkim', 'North Sikkim', 'South Sikkim', 'Pakyong', 'Soreng',
  ],
  'Tripura': [
    'West Tripura', 'Sepahijala', 'Gomati', 'South Tripura', 'Dhalai', 'Khowai', 'Unakoti',
    'North Tripura',
  ],
};
