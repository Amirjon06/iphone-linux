"""
test_iphone_linux.py — pytest test suite for iphone-linux FUSE filesystem
"""
import os, stat, pytest, subprocess

MOUNT = "/mnt/iPhone"
TEST_DIR = os.path.join(MOUNT, "Downloads", "_iphone_linux_tests")

def is_mounted():
    return subprocess.run(["mountpoint", "-q", MOUNT], capture_output=True).returncode == 0

@pytest.fixture(scope="session", autouse=True)
def require_mount():
    if not is_mounted():
        pytest.skip(f"{MOUNT} is not mounted")

@pytest.fixture(scope="session")
def test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield TEST_DIR
    for name in os.listdir(TEST_DIR):
        path = os.path.join(TEST_DIR, name)
        if os.path.isdir(path): os.rmdir(path)
        else: os.unlink(path)
    os.rmdir(TEST_DIR)

class TestMount:
    def test_root_is_dir(self): assert os.path.isdir(MOUNT)
    def test_root_has_entries(self): assert len(os.listdir(MOUNT)) > 0
    def test_known_dirs_exist(self):
        entries = os.listdir(MOUNT)
        assert "DCIM" in entries or "Downloads" in entries

class TestRead:
    def test_read_file(self, test_dir):
        p = os.path.join(test_dir, "r.txt")
        open(p,"wb").write(b"hello from linux")
        assert open(p,"rb").read() == b"hello from linux"
        os.unlink(p)
    def test_read_with_offset(self, test_dir):
        p = os.path.join(test_dir, "off.txt")
        open(p,"wb").write(b"0123456789")
        f = open(p,"rb"); f.seek(4); assert f.read(4) == b"4567"; f.close()
        os.unlink(p)
    def test_jpeg_header(self):
        dcim = os.path.join(MOUNT,"DCIM")
        if not os.path.exists(dcim): pytest.skip("No DCIM")
        for sub in os.listdir(dcim):
            for f in os.listdir(os.path.join(dcim,sub)):
                if f.upper().endswith((".JPEG",".JPG")):
                    hdr = open(os.path.join(dcim,sub,f),"rb").read(3)
                    assert hdr == b"\xff\xd8\xff"; return

class TestWrite:
    def test_write_new(self, test_dir):
        p = os.path.join(test_dir,"w.txt")
        open(p,"w").write("iphone-linux"); assert open(p).read()=="iphone-linux"; os.unlink(p)
    def test_write_binary(self, test_dir):
        p = os.path.join(test_dir,"b.bin")
        d = bytes(range(256)); open(p,"wb").write(d); assert open(p,"rb").read()==d; os.unlink(p)
    def test_write_1mb(self, test_dir):
        p = os.path.join(test_dir,"big.bin")
        d = b"x"*1024*1024; open(p,"wb").write(d)
        assert os.stat(p).st_size==len(d); os.unlink(p)
    def test_overwrite(self, test_dir):
        p = os.path.join(test_dir,"ow.txt")
        open(p,"w").write("old"); open(p,"w").write("new")
        assert open(p).read()=="new"; os.unlink(p)

class TestDirOps:
    def test_mkdir_rmdir(self, test_dir):
        d = os.path.join(test_dir,"d"); os.mkdir(d); assert os.path.isdir(d); os.rmdir(d)
    def test_unlink(self, test_dir):
        p = os.path.join(test_dir,"del.txt"); open(p,"w").write("x"); os.unlink(p)
        assert not os.path.exists(p)
    def test_rename(self, test_dir):
        s = os.path.join(test_dir,"src.txt"); d = os.path.join(test_dir,"dst.txt")
        open(s,"w").write("mv"); os.rename(s,d)
        assert not os.path.exists(s) and open(d).read()=="mv"; os.unlink(d)
