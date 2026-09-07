import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'districts.dart';
import 'strings.dart';

const apiBaseUrl = 'https://ner-shield-api.onrender.com';

// Brand palette — matches the web dashboard's dark slate + amber identity exactly
// (see frontend/src/index.css) rather than a generic Material-You seed colour.
const brandBackground = Color(0xFF0F172A);
const brandSurface = Color(0xFF1E293B);
const brandSurfaceAlt = Color(0xFF16202F);
const brandBorder = Color(0xFF334155);
const brandAmber = Color(0xFFF59E0B);
const brandAmberDark = Color(0xFFD97706);
const brandTextPrimary = Color(0xFFE2E8F0);
const brandTextMuted = Color(0xFF94A3B8);
const brandOnAmber = Color(0xFF111827);
const brandDanger = Color(0xFFEF4444);

// The same shield-and-cracked-mountain mark used for the web favicon/app icons
// (see frontend/public/logo-mark.svg), redrawn as vector paths so it renders crisp
// at any size with no bundled image asset.
class NerShieldLogo extends StatelessWidget {
  final double size;
  const NerShieldLogo({super.key, this.size = 32});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _ShieldPainter()));
}

class _ShieldPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final scale = size.width / 1024;
    canvas.scale(scale);

    final shieldPath = Path()
      ..moveTo(170, 190)
      ..lineTo(854, 190)
      ..cubicTo(930, 260, 930, 190, 930, 480)
      ..cubicTo(930, 700, 760, 850, 512, 950)
      ..cubicTo(94, 700, 264, 850, 94, 480)
      ..cubicTo(94, 190, 94, 260, 170, 190)
      ..close();

    final shieldPaint = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [brandAmber, brandAmberDark],
      ).createShader(const Rect.fromLTWH(0, 0, 1024, 1024));
    canvas.drawPath(shieldPath, shieldPaint);
    canvas.drawPath(
      shieldPath,
      Paint()
        ..color = brandBackground
        ..style = PaintingStyle.stroke
        ..strokeWidth = 10
        ..strokeJoin = StrokeJoin.round,
    );

    final mountainPath = Path()
      ..moveTo(280, 690)
      ..lineTo(432, 448)
      ..lineTo(526, 576)
      ..lineTo(612, 428)
      ..lineTo(784, 690)
      ..close();
    canvas.drawPath(mountainPath, Paint()..color = const Color(0xFFF8FAFC));

    final crackPath = Path()
      ..moveTo(526, 576)
      ..lineTo(488, 632)
      ..lineTo(536, 660)
      ..lineTo(464, 732)
      ..lineTo(512, 680)
      ..lineTo(472, 660)
      ..lineTo(526, 600)
      ..close();
    canvas.drawPath(crackPath, Paint()..color = brandBackground);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

void main() => runApp(const NerShieldApp());

class NerShieldApp extends StatefulWidget {
  const NerShieldApp({super.key});
  @override
  State<NerShieldApp> createState() => _NerShieldAppState();
}

class _NerShieldAppState extends State<NerShieldApp> {
  String language = 'en';

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((prefs) {
      final saved = prefs.getString('app_language');
      if (saved != null && supportedLanguages.contains(saved)) setState(() => language = saved);
    });
  }

  Future<void> setLanguage(String lang) async {
    setState(() => language = lang);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('app_language', lang);
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'NER-SHIELD',
    theme: buildBrandTheme(),
    home: HomeShell(language: language, onLanguageChanged: setLanguage),
  );
}

// Deliberately matches the web dashboard's dark slate + amber identity (see the brand*
// constants above) instead of Flutter's auto-generated Material-You tonal palette from a
// seed colour, which reads as generic rather than a specific, designed brand.
ThemeData buildBrandTheme() => ThemeData(
  useMaterial3: true,
  brightness: Brightness.dark,
  scaffoldBackgroundColor: brandBackground,
  colorScheme: const ColorScheme.dark(
    primary: brandAmber,
    onPrimary: brandOnAmber,
    secondary: brandAmberDark,
    onSecondary: brandOnAmber,
    surface: brandSurface,
    onSurface: brandTextPrimary,
    error: brandDanger,
  ),
  appBarTheme: const AppBarTheme(
    backgroundColor: brandBackground,
    foregroundColor: brandTextPrimary,
    surfaceTintColor: Colors.transparent,
    elevation: 0,
    centerTitle: true,
  ),
  navigationBarTheme: NavigationBarThemeData(
    backgroundColor: brandSurface,
    indicatorColor: brandAmber.withValues(alpha: 0.22),
    labelTextStyle: WidgetStateProperty.resolveWith(
      (states) => TextStyle(
        color: states.contains(WidgetState.selected) ? brandAmber : brandTextMuted,
        fontSize: 12,
        fontWeight: FontWeight.w600,
      ),
    ),
    iconTheme: WidgetStateProperty.resolveWith(
      (states) => IconThemeData(color: states.contains(WidgetState.selected) ? brandAmber : brandTextMuted),
    ),
  ),
  cardTheme: CardThemeData(
    color: brandSurface,
    elevation: 0,
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14), side: const BorderSide(color: brandBorder)),
  ),
  inputDecorationTheme: InputDecorationTheme(
    filled: true,
    fillColor: brandSurfaceAlt,
    labelStyle: const TextStyle(color: brandTextMuted),
    border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: brandBorder)),
    enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: brandBorder)),
    focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: brandAmber, width: 2)),
  ),
  filledButtonTheme: FilledButtonThemeData(
    style: FilledButton.styleFrom(
      backgroundColor: brandAmber,
      foregroundColor: brandOnAmber,
      textStyle: const TextStyle(fontWeight: FontWeight.bold),
      padding: const EdgeInsets.symmetric(vertical: 14),
    ),
  ),
  outlinedButtonTheme: OutlinedButtonThemeData(
    style: OutlinedButton.styleFrom(
      foregroundColor: brandTextPrimary,
      side: const BorderSide(color: brandBorder),
      padding: const EdgeInsets.symmetric(vertical: 12),
    ),
  ),
  popupMenuTheme: PopupMenuThemeData(color: brandSurface, surfaceTintColor: Colors.transparent),
  dropdownMenuTheme: DropdownMenuThemeData(
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: brandSurfaceAlt,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: brandBorder)),
    ),
  ),
  textTheme: const TextTheme(bodyMedium: TextStyle(color: brandTextPrimary)).apply(
    bodyColor: brandTextPrimary,
    displayColor: brandTextPrimary,
  ),
  iconTheme: const IconThemeData(color: brandTextPrimary),
  dividerColor: brandBorder,
  listTileTheme: const ListTileThemeData(iconColor: brandTextMuted, textColor: brandTextPrimary),
);

class HomeShell extends StatefulWidget {
  final String language;
  final ValueChanged<String> onLanguageChanged;
  const HomeShell({super.key, required this.language, required this.onLanguageChanged});
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int tab = 0;

  @override
  Widget build(BuildContext context) {
    final lang = widget.language;
    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const NerShieldLogo(size: 30),
            const SizedBox(width: 10),
            const Text(
              'NER-SHIELD',
              style: TextStyle(fontWeight: FontWeight.w800, letterSpacing: -0.3, fontSize: 20),
            ),
          ],
        ),
        actions: [
          PopupMenuButton<String>(
            icon: const Icon(Icons.language),
            tooltip: t(lang, 'language'),
            onSelected: widget.onLanguageChanged,
            itemBuilder: (context) => supportedLanguages
                .map((code) => PopupMenuItem(value: code, child: Text(languageNames[code] ?? code)))
                .toList(),
          ),
          if (tab == 1) IconButton(onPressed: () => _alertsKey.currentState?.refresh(), icon: const Icon(Icons.refresh)),
        ],
      ),
      body: IndexedStack(index: tab, children: [
        ReportPage(language: lang),
        AlertsPage(key: _alertsKey, language: lang),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        onDestinationSelected: (i) => setState(() => tab = i),
        destinations: [
          NavigationDestination(icon: const Icon(Icons.report_outlined), label: t(lang, 'nav_report')),
          NavigationDestination(icon: const Icon(Icons.notifications_outlined), label: t(lang, 'nav_alerts')),
        ],
      ),
    );
  }

  final _alertsKey = GlobalKey<_AlertsPageState>();
}

Future<String?> uploadMedia(XFile file) async {
  final extension = file.path.split('.').last.toLowerCase();
  final contentTypeByExt = {
    'jpg': MediaType('image', 'jpeg'), 'jpeg': MediaType('image', 'jpeg'),
    'png': MediaType('image', 'png'), 'webp': MediaType('image', 'webp'),
    'mp4': MediaType('video', 'mp4'), 'webm': MediaType('video', 'webm'),
  };
  final contentType = contentTypeByExt[extension];
  if (contentType == null) return null;
  try {
    final request = http.MultipartRequest('POST', Uri.parse('$apiBaseUrl/api/v1/uploads'));
    request.files.add(await http.MultipartFile.fromPath('file', file.path, contentType: contentType));
    final streamed = await request.send().timeout(const Duration(seconds: 30));
    if (streamed.statusCode != 200) return null;
    final body = jsonDecode(await streamed.stream.bytesToString()) as Map<String, dynamic>;
    return '$apiBaseUrl${body['url']}';
  } catch (_) {
    return null;
  }
}

class ReportPage extends StatefulWidget {
  final String language;
  const ReportPage({super.key, required this.language});
  @override
  State<ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<ReportPage> {
  final description = TextEditingController();
  String selectedState = 'Meghalaya';
  String selectedDistrict = 'East Khasi Hills';
  String reportType = 'crack';
  String severity = 'moderate';
  String? status;
  double? latitude;
  double? longitude;
  XFile? media;
  bool busy = false;

  String get lang => widget.language;

  Future<void> captureLocation() async {
    setState(() => status = t(lang, 'getting_location'));
    try {
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied || permission == LocationPermission.deniedForever) {
        setState(() => status = t(lang, 'location_denied'));
        return;
      }
      if (!await Geolocator.isLocationServiceEnabled()) {
        setState(() => status = t(lang, 'location_off'));
        return;
      }
      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      ).timeout(const Duration(seconds: 15));
      setState(() {
        latitude = position.latitude;
        longitude = position.longitude;
        status = '${t(lang, 'location_captured')}: ${latitude!.toStringAsFixed(5)}, ${longitude!.toStringAsFixed(5)}';
      });
    } catch (_) {
      setState(() => status = t(lang, 'location_failed'));
    }
  }

  Future<void> pickMedia(ImageSource source, {bool video = false}) async {
    final picker = ImagePicker();
    final file = video ? await picker.pickVideo(source: source) : await picker.pickImage(source: source, imageQuality: 80);
    if (file != null) setState(() { media = file; status = '${t(lang, 'media_attached')}: ${file.name}'; });
  }

  Map<String, dynamic> payload(String? imageUrl) => {
    'report_type': reportType, 'severity': severity, 'description': description.text,
    'latitude': latitude ?? 25.5788, 'longitude': longitude ?? 91.8933,
    'district': selectedDistrict,
    'road_status': 'restricted', 'reporter_role': 'citizen',
    if (imageUrl != null) 'image_url': imageUrl,
  };

  Future<void> saveOffline(Map<String, dynamic> item) async {
    final prefs = await SharedPreferences.getInstance();
    final queue = prefs.getStringList('report_queue') ?? [];
    queue.add(jsonEncode(item));
    await prefs.setStringList('report_queue', queue);
  }

  Future<bool> send(Map<String, dynamic> item) async {
    try {
      final response = await http.post(Uri.parse('$apiBaseUrl/api/v1/reports'),
          headers: {'Content-Type': 'application/json'}, body: jsonEncode(item)).timeout(const Duration(seconds: 10));
      return response.statusCode == 201;
    } catch (_) { return false; }
  }

  Future<void> submit() async {
    if (description.text.trim().length < 3) { setState(() => status = t(lang, 'describe_min')); return; }
    setState(() => busy = true);
    String? imageUrl;
    if (media != null) {
      imageUrl = await uploadMedia(media!);
      if (imageUrl == null) status = t(lang, 'upload_failed');
    }
    final item = payload(imageUrl);
    final sent = await send(item);
    if (!sent) await saveOffline(item);
    setState(() {
      busy = false;
      status = sent ? t(lang, 'report_sent') : t(lang, 'report_offline');
      if (sent) { description.clear(); media = null; }
    });
  }

  Future<void> syncQueue() async {
    final prefs = await SharedPreferences.getInstance();
    final queue = prefs.getStringList('report_queue') ?? [];
    final remaining = <String>[];
    for (final raw in queue) { if (!await send(jsonDecode(raw))) remaining.add(raw); }
    await prefs.setStringList('report_queue', remaining);
    setState(() => status = '${t(lang, 'sync_finished')} ${remaining.length} ${t(lang, 'still_queued')}');
  }

  @override
  Widget build(BuildContext context) => Padding(padding: const EdgeInsets.all(16), child: ListView(children: [
    DropdownButtonFormField(initialValue: reportType, items: ['crack','slope_movement','landslide','blocked_road','rockfall','flooding']
      .map((v) => DropdownMenuItem(value: v, child: Text(t(lang, v)))).toList(), onChanged: (v) => setState(() => reportType = v!)),
    const SizedBox(height: 12),
    DropdownButtonFormField(initialValue: severity, items: ['low','moderate','high','critical']
      .map((v) => DropdownMenuItem(value: v, child: Text(t(lang, v)))).toList(), onChanged: (v) => setState(() => severity = v!)),
    const SizedBox(height: 12),
    DropdownButtonFormField(
      initialValue: selectedState,
      decoration: InputDecoration(labelText: t(lang, 'state_label'), border: const OutlineInputBorder()),
      items: nerDistricts.keys.map((s) => DropdownMenuItem(value: s, child: Text(s))).toList(),
      onChanged: (v) => setState(() {
        selectedState = v!;
        selectedDistrict = nerDistricts[v]!.first;
      }),
    ),
    const SizedBox(height: 12),
    DropdownButtonFormField(
      initialValue: selectedDistrict,
      decoration: InputDecoration(labelText: t(lang, 'district_label'), border: const OutlineInputBorder()),
      items: nerDistricts[selectedState]!.map((d) => DropdownMenuItem(value: d, child: Text(d))).toList(),
      onChanged: (v) => setState(() => selectedDistrict = v!),
    ),
    const SizedBox(height: 12),
    TextField(controller: description, maxLines: 4, decoration: InputDecoration(labelText: t(lang, 'description_label'), border: const OutlineInputBorder())),
    const SizedBox(height: 12),
    OutlinedButton.icon(onPressed: captureLocation, icon: const Icon(Icons.my_location), label: Text(
      latitude != null ? '${t(lang, 'location_prefix')}: ${latitude!.toStringAsFixed(4)}, ${longitude!.toStringAsFixed(4)}' : t(lang, 'capture_location'),
    )),
    const SizedBox(height: 8),
    Row(children: [
      Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.camera), icon: const Icon(Icons.photo_camera), label: Text(t(lang, 'photo')))),
      const SizedBox(width: 8),
      Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.gallery), icon: const Icon(Icons.photo_library), label: Text(t(lang, 'gallery')))),
    ]),
    const SizedBox(height: 8),
    Row(children: [
      Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.camera, video: true), icon: const Icon(Icons.videocam), label: Text(t(lang, 'video')))),
    ]),
    if (media != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text('${t(lang, 'attached')}: ${media!.name}')),
    const SizedBox(height: 16),
    FilledButton(onPressed: busy ? null : submit, child: Text(busy ? t(lang, 'submitting') : t(lang, 'submit'))),
    OutlinedButton(onPressed: busy ? null : syncQueue, child: Text(t(lang, 'sync'))),
    const SizedBox(height: 12), Text(status ?? t(lang, 'ready')),
  ]));
}

class AlertsPage extends StatefulWidget {
  final String language;
  const AlertsPage({super.key, required this.language});
  @override
  State<AlertsPage> createState() => _AlertsPageState();
}

class _AlertsPageState extends State<AlertsPage> {
  List<dynamic> alerts = [];
  String? status;

  String get lang => widget.language;

  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() => status = t(lang, 'loading'));
    try {
      final response = await http.get(Uri.parse('$apiBaseUrl/api/v1/alerts')).timeout(const Duration(seconds: 10));
      if (response.statusCode == 200) {
        setState(() { alerts = jsonDecode(response.body) as List<dynamic>; status = alerts.isEmpty ? t(lang, 'no_alerts') : null; });
      } else {
        setState(() => status = t(lang, 'alerts_load_error'));
      }
    } catch (_) {
      setState(() => status = t(lang, 'alerts_offline'));
    }
  }

  @override
  Widget build(BuildContext context) => RefreshIndicator(
    onRefresh: refresh,
    child: alerts.isEmpty
        ? ListView(children: [Padding(padding: const EdgeInsets.all(24), child: Text(status ?? ''))])
        : ListView.builder(
            itemCount: alerts.length,
            itemBuilder: (context, i) {
              final a = alerts[i] as Map<String, dynamic>;
              return ListTile(
                leading: Icon(Icons.warning, color: {
                  'critical': Colors.red, 'high': Colors.orange, 'moderate': Colors.amber, 'low': Colors.green,
                }[a['severity']] ?? Colors.grey),
                title: Text('${(a['severity'] as String).toUpperCase()} · ${a['district'] ?? t(lang, 'unknown_location')}'),
                subtitle: Text(a['message'] as String),
              );
            },
          ),
  );
}
