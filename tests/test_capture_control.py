"""Exercise the private capture scope, quota and HTTP access boundaries."""
import ctypes
import ctypes.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "capture"))
sys.path.insert(0, str(ROOT / "apps" / "raspberrypi"))
import ulab_capture as cap


def pcap_library():
    for name in (ctypes.util.find_library("pcap"), "libpcap.so.1", "libpcap.so.0.8", "wpcap.dll"):
        if name:
            try:
                return ctypes.CDLL(name)
            except OSError:
                continue
    return None


class CaptureScope(unittest.TestCase):
    def setUp(self):
        self.info = {"gadget": {"interface": "test0", "filter": "host 192.0.2.2 and tcp port 43123"},
                     "loopback": {"interface": "lo", "filter": "tcp port 43124"},
                     "usbmon": {"interface": "usbmon3", "filter": cap.usbmon_filter(2, (0x04, 0x85))}}

    def test_user_filter_can_only_narrow_the_lab_filter(self):
        interface, expression = cap.scope(self.info, "gadget", "tcp.len > 1 or ip", False)
        self.assertEqual(interface, "test0")
        self.assertTrue(expression.startswith("(host 192.0.2.2 and tcp port 43123) and ("))
        with self.assertRaises(ValueError):
            cap.scope(self.info, "gadget", ") or ip or (tcp", False)
        with self.assertRaises(ValueError):
            cap.scope(self.info, "any", "", False)

    def test_usb_diagnostics_are_explicit_and_device_bound(self):
        with self.assertRaises(ValueError):
            cap.scope(self.info, "usbmon", "", False)
        self.assertEqual(cap.scope(self.info, "usbmon", "", True), ("usbmon3", self.info["usbmon"]["filter"]))

    def test_only_active_apple_mux_bulk_pair_is_in_usbmon_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            class FixturePath(type(Path())):
                # The Windows filesystem reserves the colon used by sysfs interface names.
                def glob(self, pattern):
                    return super().glob(pattern.replace(":", "_"))

            device = FixturePath(temp)/"3-1"
            device.mkdir()
            for key,value in {"bConfigurationValue":"4","authorized":"1","busnum":"3","devnum":"2"}.items():
                (device/key).write_text(value)

            def interface(name, identity, endpoints):
                node = device/(device.name+"_"+name)
                node.mkdir()
                for key,value in zip(("bInterfaceClass","bInterfaceSubClass","bInterfaceProtocol"),identity):
                    (node/key).write_text(value)
                (node/"bNumEndpoints").write_text(str(len(endpoints)))
                for addr,attr in endpoints:
                    ep = node/("ep_"+addr)
                    ep.mkdir()
                    (ep/"bEndpointAddress").write_text(addr)
                    (ep/"bmAttributes").write_text(attr)
                return node

            interface("4.0", ["06","01","01"], [("02","02"),("81","02"),("83","03")])
            mux = interface("4.1", ["ff","fe","02"], [("04","02"),("85","02")])
            interface("4.2", ["ff","fd","01"], [("05","02"),("86","02")])
            interface("1.1", ["ff","fe","02"], [("01","02"),("82","02")])
            pair = cap.mux_bulk_endpoints(device)
            self.assertEqual(pair, (4, 133))
            self.assertEqual(cap.usbmon_filter(2, pair), "link[11] = 2 and link[9] = 3 and (link[10] = 4 or link[10] = 133)")
            (mux/"ep_85"/"bmAttributes").write_text("03")
            self.assertIsNone(cap.mux_bulk_endpoints(device))
            (mux/"ep_85"/"bmAttributes").write_text("02")
            (mux/"ep_85"/"bEndpointAddress").write_text("95")
            self.assertIsNone(cap.mux_bulk_endpoints(device))
            (mux/"ep_85"/"bEndpointAddress").write_text("85")
            interface("4.3", ["ff","fe","02"], [("07","02"),("88","02")])
            self.assertIsNone(cap.mux_bulk_endpoints(device))
            (device/"bConfigurationValue").write_text("3")
            self.assertIsNone(cap.mux_bulk_endpoints(device))
            (device/"authorized").write_text("0")
            self.assertIsNone(cap.mux_bulk_endpoints(device))

    def test_compiled_bpf_accepts_only_selected_bulk_urbs_on_both_usb_header_formats(self):
        pcap = pcap_library()
        if pcap is None:
            self.skipTest("libpcap required for offline BPF execution")
        class Program(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("instructions", ctypes.c_void_p)]
        class PacketHeader(ctypes.Structure):
            _fields_ = [("seconds", ctypes.c_long), ("usec", ctypes.c_long),
                        ("caplen", ctypes.c_uint32), ("length", ctypes.c_uint32)]
        pcap.pcap_open_dead.argtypes = [ctypes.c_int, ctypes.c_int]
        pcap.pcap_open_dead.restype = ctypes.c_void_p
        pcap.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(Program), ctypes.c_char_p,
                                      ctypes.c_int, ctypes.c_uint32]
        pcap.pcap_offline_filter.argtypes = [ctypes.POINTER(Program), ctypes.POINTER(PacketHeader), ctypes.c_void_p]
        pcap.pcap_freecode.argtypes = [ctypes.POINTER(Program)]
        pcap.pcap_close.argtypes = [ctypes.c_void_p]
        expression = cap.usbmon_filter(2, (0x04, 0x85)).encode()
        for dlt, header_size in ((189,48), (220,64)):
            handle = pcap.pcap_open_dead(dlt, 262144)
            self.assertTrue(handle)
            prog = Program()
            try:
                self.assertEqual(pcap.pcap_compile(handle, ctypes.byref(prog), expression, 1, 0xffffffff), 0)
                hdr = PacketHeader(0,0,header_size,header_size)
                for ep, xfer, device, want in ((4,3,2,True), (0x85,3,2,True), (4,3,7,False),
                        (4,2,2,False), (0x81,3,2,False), (0x86,3,2,False), (0,2,2,False)):
                    raw = bytearray(header_size)
                    raw[8:12] = bytes([ord("S"), xfer, ep, device])
                    buf = ctypes.create_string_buffer(bytes(raw))
                    actual = pcap.pcap_offline_filter(ctypes.byref(prog), ctypes.byref(hdr), buf)
                    self.assertEqual(bool(actual), want, (dlt,ep,xfer,device))
            finally:
                pcap.pcap_freecode(ctypes.byref(prog))
                pcap.pcap_close(handle)

    def test_per_session_disk_and_time_bounds(self):
        for value in (0, 121, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cap.bounded(value, 120)
        self.assertEqual(cap.bounded(120, 120), 120)

    def test_capture_list_ignores_links_and_non_session_names(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / ("a" * 32 + ".pcapng")).write_bytes(b"x")
            (path / "personal.pcapng").write_bytes(b"x")
            self.assertEqual([p.name for p in cap.files(path)], ["a" * 32 + ".pcapng"])


if __name__ == "__main__":
    unittest.main()
