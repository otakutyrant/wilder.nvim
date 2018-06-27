import time
import multiprocessing
import functools
import importlib
import concurrent.futures
import threading
import asyncio
import neovim

@neovim.plugin
class Wildsearch(object):
    def __init__(self, nvim):
        self.nvim = nvim
        self.queue = multiprocessing.Queue()
        self.events = []
        self.lock = threading.Lock()
        self.executor = None

    def do(self, ctx, x):
        self.nvim.call('wildsearch#pipeline#do', ctx, x)

    def echo(self, x):
        self.nvim.session.threadsafe_call(lambda: self.nvim.command('echom "' + x + '"'))

    def run_in_background(self, fn, args):
        self.executor.submit(
            functools.partial(
                fn,
                *args,
            )
        )

    def consumer(self):
        while True:
            args = self.queue.get()
            while not self.queue.empty():
                args = self.queue.get_nowait()
            self.nvim.async_call(self.do, args[0], args[1])

    @neovim.function('_wildsearch_init', sync=True, allow_nested=True)
    def init(self, args):
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        t = threading.Thread(target=self.consumer, daemon=True)
        t.start()

    @neovim.function('_wildsearch_python_sleep', sync=False)
    def sleep(self, args):
        self.run_in_background(self.sleep_handler, args)

    def sleep_handler(self, t, ctx, x):
        time.sleep(t)
        self.do(ctx, x)

    @neovim.function('_wildsearch_python_search', sync=False, allow_nested=True)
    def search(self, args):
        if args[2] == "":
            self.do(args[1], [])
            return

        buf = self.nvim.current.buffer[:].copy()

        event = threading.Event()

        with self.lock:
            while len(self.events) > 1:
                e = self.events.pop(0)
                e.set()

            self.events.append(event)

        self.run_in_background(self.search_handler, [event, buf] + args)
        #  t = threading.Thread(target=lambda: self.search_handler(event, buf, *args))
        #  t.start()
        #  with self.executor as executor:
            #  executor.submit(self.search_handler(event, buf, *args))

    def search_handler(self, event, buf, opts, ctx, x):
        if event.is_set():
            return
        
        module_name = opts['engine'] if 'engine' in opts else 're'
        max_candidates = opts['max_candidates'] if 'max_candidates' in opts else -1

        candidates = []

        try:
            re = importlib.import_module(module_name)
            pattern = re.compile(x)

            for line in buf:
                if event.is_set():
                    return
                for match in pattern.finditer(line):
                    if event.is_set():
                        return
                    candidates.append(match.group())
                    if max_candidates > 0 and len(candidates) >= max_candidates:
                        self.queue.put((ctx, candidates,))
                        return
            self.queue.put((ctx, candidates,))
        except Exception as e:
            self.queue.put((ctx, {'wildsearch_error': str(e)},))
        finally:
            with self.lock:
                self.events.remove(event)

    @neovim.function('_wildsearch_python_uniq', sync=False)
    def uniq(self, args):
        ctx = args[0]
        items = args[1]

        try:
            self.queue.put((ctx, list(set(items)),))
        except Exception as e:
            self.queue.put((ctx, {'wildsearch_error': str(e)},))

    @neovim.function('_wildsearch_python_sort', sync=False, allow_nested=True)
    def sort(self, args):
        opts = args[0]
        ctx = args[1]
        items = args[2]
        use_fuzzy = opts['fuzzy'] if 'fuzzy' in opts else False

        try:
            if use_fuzzy:
                fuzz = importlib.import_module('fuzzywuzzy.fuzz')
                res = sorted(items, key=lambda x: fuzz.ratio(ctx['input'], x))
            else:
                res = sorted(items)

            self.queue.put((ctx, res,))
        except Exception as e:
            self.queue.put((ctx, {'wildsearch_error': str(e)},))
