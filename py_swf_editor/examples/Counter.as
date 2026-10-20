// First native class tier: Object-derived library, not a stage/game runtime.
package demo {
    [Inspectable(name="counter")]
    public final class Counter {
        private var total:int = 0;
        public static const VERSION:String = "v1";

        public function Counter(start:int = 0) {
            super();
            this.total = start;
        }

        public function add(n:int = 1):int {
            this.total = this.total + n;
            return this.total;
        }

        public function get value():int {
            return this.total;
        }

        public function set value(n:int):void {
            this.total = n;
        }
    }
}
